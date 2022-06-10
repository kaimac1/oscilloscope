from tidal import *
from buttons import Buttons
from app import TextApp
import machine
from machine import ADC
import time

import vga2_8x8 as font

GREY = color565(0x20, 0x20, 0x20)

SAMPLES = 128       # width of scope area = number of samples
SCOPE_Y = 0
SCOPE_HEIGHT = 128

SCALES = [[ADC.ATTN_0DB, 100], [ADC.ATTN_6DB, 200], [ADC.ATTN_11DB, 400]]
TIMEBASES = [[31, 1], [62, 2], [156, 5], [312, 10], [625, 20], [1562, 50], [3125, 100], [6250, 200], [15625, 500], [31250, 1000]]
PX_PER_VDIV = 16
PX_PER_HDIV = 32

MAX_TRIGGER_CYCLES = 128
trigger_level = 64



class MyApp(TextApp):
    BG = BLACK
    FG = WHITE

    def on_start(self):
        super().on_start()
        self.set_rotation(270)

    def on_activate(self):
        super().on_activate() # This will clear the screen by calling TextWindow.redraw()
        display.fill_rect(SAMPLES, 0, 240-SAMPLES, 135, GREY)

        self.scale = 0
        self.timebase = 4
        self.adc_init()
        self.buffer0 = bytearray(SAMPLES)
        self.buffer1 = bytearray(SAMPLES)

        self.outpin = G3
        self.outpin.init(self.outpin.OUT, self.outpin.PULL_DOWN)
        self.outpin.value(0)

        self.buttons.set_rotation(0) # Something seems to be broken with the buttons when the screen is rotated 90/270 degrees...
        self.buttons.on_press(JOY_LEFT, lambda: self.scale_set(-1))
        self.buttons.on_press(JOY_RIGHT, lambda: self.scale_set(1))
        self.buttons.on_press(JOY_UP, lambda: self.timebase_set(-1))
        self.buttons.on_press(JOY_DOWN, lambda: self.timebase_set(1))

        self.after(10, self.acquisition_start)

    def scale_set(self, ds):
        if 0 <= (self.scale + ds) < len(SCALES):
            self.scale += ds
        self.adc_init()
    def timebase_set(self, dt):
        if 0 <= (self.timebase + dt) < len(TIMEBASES):
            self.timebase += dt
        self.adc_init()

    def adc_init(self):
        self.adc0 = ADC(G0, atten=SCALES[self.scale][0])
        #self.adc1 = ADC(G1, atten=SCALES[self.scale][0])
        self.vscaling = int((SCALES[self.scale][1] * 1000) / PX_PER_VDIV)

        display.text(font, "{: 4} mV/div".format(SCALES[self.scale][1]), 148, 4, YELLOW, GREY)
        display.text(font, "{: 4} ms/div".format(TIMEBASES[self.timebase][1]), 148, 16, YELLOW, GREY)

    # Acquisition

    def acquire_channels(self):
        ts_microseconds = TIMEBASES[self.timebase][0]
        for i in range(SAMPLES):
            t0 = time.ticks_us()
            self.buffer0[i] = self.adc0.read_uv() // self.vscaling
            #self.buffer1[i] = self.adc1.read_uv() // self.vscaling
            t_adc = time.ticks_diff(time.ticks_us(), t0)
            time.sleep_us(ts_microseconds - t_adc)

    def acquire_buffer(self, buffer, n, timebase_setting):
        ts_microseconds = TIMEBASES[timebase_setting][0]

        # 1 ms/div requires supa hax
        if timebase_setting == 0:
            x = 0
            for i in range(n):
                buffer[i] = self.adc0.read_uv() // self.vscaling
                x = i * 0.33 # nop()
            return

        for i in range(n):
            t0 = time.ticks_us()
            buffer[i] = self.adc0.read_uv() // self.vscaling
            # Improve timebase accuracy by factoring in the acquisition time
            # 50 Hz noise on 20ms/div setting should line up with the grid lines
            t_adc = time.ticks_diff(time.ticks_us(), t0)
            time.sleep_us(ts_microseconds - t_adc)

    def acquisition_start(self):        
        trig_attempts = 0
        trig_state = 0
        triggered = 0
        trigbuf = bytearray(2)
        while trig_attempts < MAX_TRIGGER_CYCLES:
            self.acquire_buffer(trigbuf, 2, 0)
            if trig_state == 0:
                triggered = 1
                for val in trigbuf:
                    triggered = triggered & (val < trigger_level)
                if triggered: trig_state = 1
            elif trig_state == 1:
                triggered = 1
                for val in trigbuf:
                    triggered = triggered & (val >= trigger_level)
                if triggered: break

            trig_attempts += 1

        self.acquire_buffer(self.buffer0, SAMPLES, self.timebase)
        #self.acquire_channels()
        self.after(0, self.draw_samples)


    # Drawing

    def draw_samples(self):
        # To reduce flicker, clear and redraw the trace in chunks

        display.fill_rect(0, SCOPE_Y, SAMPLES, SCOPE_HEIGHT, BLACK)
        for j in range(8):
            display.line(0, SCOPE_HEIGHT-j*PX_PER_VDIV, SAMPLES, SCOPE_HEIGHT-j*PX_PER_VDIV, GREY)
        for j in range(4):
            display.line(j*PX_PER_HDIV-1, 0, j*PX_PER_HDIV-1, SCOPE_HEIGHT, GREY)


        prev_y = SCOPE_Y+SCOPE_HEIGHT-1 - self.buffer0[0]
        prev_x = 0
        for x in range(SAMPLES):
            y = SCOPE_Y+SCOPE_HEIGHT-1 - self.buffer0[x]
            display.line(prev_x, prev_y, x, y, YELLOW)
            prev_x = x
            prev_y = y

        # prev_y = SCOPE_Y+SCOPE_HEIGHT-1 - self.buffer1[0]
        # prev_x = 0
        # for x in range(SAMPLES):
        #     y = SCOPE_Y+SCOPE_HEIGHT-1 - self.buffer1[x]
        #     display.line(prev_x, prev_y, x, y, GREEN)
        #     prev_x = x
        #     prev_y = y

        # BLOCKSZ = 32
        # BLOCKS = int(SAMPLES / BLOCKSZ)
        # for block in range(BLOCKS):
        #     x0 = block*BLOCKSZ

        #     display.fill_rect(x0, SCOPE_Y, BLOCKSZ, SCOPE_HEIGHT, BLACK)


        #     pidx = 0
        #     if x0 > 0:
        #         pidx = x0 - 1
        #     prev_y = SCOPE_Y+SCOPE_HEIGHT-1 - self.buffer[pidx]
        #     prev_x = x0
        #     for x1 in range(BLOCKSZ):
        #         x = x0 + x1
        #         y = SCOPE_Y+SCOPE_HEIGHT-1 - self.buffer[x]
        #         display.line(prev_x, prev_y, x, y, MyApp.FG)
        #         prev_x = x
        #         prev_y = y

        self.after(50, self.acquisition_start)

main = MyApp