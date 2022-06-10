from tidal import *
from buttons import Buttons
from app import TextApp
import machine
from machine import ADC
import time

import vga2_8x8 as font

GREY = color565(0x20, 0x20, 0x20)

SAMPLES = 128       # width of scope area = number of samples
SCOPE_HEIGHT = 128

SCALES = [[ADC.ATTN_0DB, 100], [ADC.ATTN_6DB, 200], [ADC.ATTN_11DB, 400]]
TIMEBASES = [[31, 1], [62, 2], [156, 5], [312, 10], [625, 20], [1562, 50], [3125, 100], [6250, 200], [15625, 500], [31250, 1000]]
PX_PER_VDIV = 16
PX_PER_HDIV = 32

MAX_TRIGGER_CYCLES = 256



class MyApp(TextApp):
    BG = BLACK
    FG = WHITE

    def on_start(self):
        super().on_start()
        self.set_rotation(270)

    def on_activate(self):
        super().on_activate() # This will clear the screen by calling TextWindow.redraw()
        display.fill_rect(SAMPLES, 0, 240-SAMPLES, SCOPE_HEIGHT+1, GREY)

        self.scale = 0
        self.timebase = 4
        self.trig = False
        self.trig_voltage = 0.4
        self.adc_init()
        self.buffer0 = bytearray(SAMPLES)

        self.outpin = G3
        self.outpin.init(self.outpin.OUT, self.outpin.PULL_DOWN)
        self.outpin.value(0)

        self.buttons.set_rotation(0) # Something seems to be broken with the buttons when the screen is rotated 90/270 degrees...
        # self.buttons.on_press(BUTTON_A, lambda: self.buttona())
        self.buttons.on_press(JOY_LEFT, lambda: self.btn_ud(-1))
        self.buttons.on_press(JOY_RIGHT, lambda: self.btn_ud(1))
        self.buttons.on_press(JOY_UP, lambda: self.timebase_set(-1))
        self.buttons.on_press(JOY_DOWN, lambda: self.timebase_set(1))

        self.after(10, self.acquisition_start)


    def btn_ud(self, dx):
        if BUTTON_A.value() == 1:
            self.scale_set(dx)
        else:
            # A held
            self.trigger_set(dx)

    def scale_set(self, ds):
        if 0 <= (self.scale + ds) < len(SCALES):
            self.scale += ds
        self.adc_init()
    def timebase_set(self, dt):
        if 0 <= (self.timebase + dt) < len(TIMEBASES):
            self.timebase += dt
        self.adc_init()
    def trigger_set(self, dt):
        self.trig_voltage += dt * 0.5 * SCALES[self.scale][1] / 1000
        self.draw_info()

    def adc_init(self):
        self.adc0 = ADC(G0, atten=SCALES[self.scale][0])
        self.vscaling = int((SCALES[self.scale][1] * 1000) / PX_PER_VDIV)
        self.draw_info()



    # Acquisition

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
        trigbuf = bytearray(1)
        trig_level = int(self.trig_voltage * 1000000) // self.vscaling
        while trig_attempts < MAX_TRIGGER_CYCLES:
            self.acquire_buffer(trigbuf, 1, 0)
            if trig_state == 0:
                triggered = 1
                for val in trigbuf:
                    triggered = triggered & (val < trig_level)
                if triggered: trig_state = 1
            elif trig_state == 1:
                triggered = 1
                for val in trigbuf:
                    triggered = triggered & (val >= trig_level)
                self.trig = triggered
                if triggered: break

            trig_attempts += 1

        self.acquire_buffer(self.buffer0, SAMPLES, self.timebase)
        self.after(0, self.draw_samples)


    # Drawing

    def draw_info(self):
        display.text(font, "{: 4} mV/div".format(SCALES[self.scale][1]), 148, 16, YELLOW, GREY)
        display.text(font, "{: 4} ms/div".format(TIMEBASES[self.timebase][1]), 148, 28, YELLOW, GREY)
        display.text(font, "Trig: {:.2f} V".format(self.trig_voltage), 140, 40, YELLOW, GREY)

    def draw_samples(self):
        # To reduce flicker, clear and redraw one horizontal division at a time
        trig_level = int(self.trig_voltage * 1000000) // self.vscaling
        HDIVS = 4
        for div in range(HDIVS):
            x0 = div * PX_PER_HDIV
            display.fill_rect(x0, 0, PX_PER_HDIV, SCOPE_HEIGHT, BLACK)
            
            # Vertical gridlines
            display.line(x0+PX_PER_HDIV-1, 0, x0+PX_PER_HDIV-1, SCOPE_HEIGHT, GREY)
            
            # Horizontal gridlines
            for j in range(8):
                display.line(x0, SCOPE_HEIGHT-j*PX_PER_VDIV, x0+PX_PER_HDIV, SCOPE_HEIGHT-j*PX_PER_VDIV, GREY)
            
            # Trigger level
            display.line(x0, SCOPE_HEIGHT-1-trig_level, x0+PX_PER_HDIV-1, SCOPE_HEIGHT-1-trig_level, GREEN)
            
            # Trace
            pidx = 0
            if x0 > 0:
                pidx = x0 - 1
            prev_y = SCOPE_HEIGHT-1 - self.buffer0[pidx]
            prev_x = x0
            for x1 in range(PX_PER_HDIV):
                x = x0 + x1
                y = SCOPE_HEIGHT-1 - self.buffer0[x]
                display.line(prev_x, prev_y, x, y, YELLOW)
                prev_x = x
                prev_y = y



        # Sidebar
        display.text(font, "Trig'd" if self.trig else "      ", 132, 4, GREEN, GREY)

        self.after(50, self.acquisition_start)

main = MyApp