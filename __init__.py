from tidal import *
from buttons import Buttons
from app import TextApp
import machine
from machine import ADC
import time
import lodepng

import vga2_8x8 as font

PATH = "/apps/scope/"

GREY = color565(0x20, 0x20, 0x20)

SAMPLES = 128       # width of scope area = number of samples
SCOPE_HEIGHT = 128

SCALES = [[ADC.ATTN_0DB, 100], [ADC.ATTN_6DB, 200], [ADC.ATTN_11DB, 400]]
TIMEBASES = [[31, 1], [62, 2], [156, 5], [312, 10], [625, 20], [1562, 50], [3125, 100], [6250, 200], [0, 1000], [0, 2000], [0, 5000], [0, 10000], [0, 20000], [0, 60000]]
ACQUIRE_MODE_ASYNC_THRESH = 101
PX_PER_VDIV = 16
PX_PER_HDIV = 32

TRIGGER_WIDTH = 1
MAX_TRIGGER_CYCLES = 256



class Oscilloscope(TextApp):
    BG = BLACK
    FG = WHITE

    def on_start(self):
        self.set_rotation(270)
        super().on_start()

    def on_activate(self):
        super().on_activate()

        (w, h, buf) = lodepng.decode565(PATH + "badgilent.png")
        display.blit_buffer(buf, 0, 0, w, h)
        time.sleep(2.5)
        display.fill(BLACK)
        display.fill_rect(SAMPLES, 0, 240-SAMPLES, SCOPE_HEIGHT+1, GREY)

        self.should_quit = False
        self.scale = 0
        self.timebase = 4
        self.trig = False
        self.trig_voltage = 0.4
        self.buffer0 = bytearray(SAMPLES)
        self.roll_mode = False
        self.roll_display_cnt = 0
        self.adc_init()
        self.source = self.get_adc
        self.draw_info()

        self.buttons.set_rotation(0) # Something seems to be broken with the buttons when the screen is rotated 90/270 degrees...
        # self.buttons.on_press(BUTTON_A, lambda: self.buttona())
        self.buttons.on_press(JOY_LEFT, lambda: self.btn_ud(-1))
        self.buttons.on_press(JOY_RIGHT, lambda: self.btn_ud(1))
        self.buttons.on_press(JOY_UP, lambda: self.timebase_set(-1))
        self.buttons.on_press(JOY_DOWN, lambda: self.timebase_set(1))
        
        self.start_new_acquisition()

    def start_new_acquisition(self):
        if not self.roll_mode and not self.should_quit:
            self.acq_timer = self.after(0, self.acquisition_start)

    def on_deactivate(self):
        self.should_quit = True
        self.acq_timer.cancel()

    def clear_buffer(self):
        self.buffer0 = bytearray(SAMPLES)

    def px_to_volts(self, val):
        return val / PX_PER_VDIV * (SCALES[self.scale][1] / 1000)

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
            if self.roll_mode:
                self.clear_buffer()
            self.draw_info()

    def timebase_set(self, dt):
        if 0 <= (self.timebase + dt) < len(TIMEBASES):
            self.timebase += dt
            self.adc_init()

            if TIMEBASES[self.timebase][0] == 0:
                self.roll_mode_init(True, TIMEBASES[self.timebase][1])
            else:
                self.roll_mode_init(False)
            self.draw_info()

    def trigger_set(self, dt):
        self.trig_voltage += dt * 0.5 * SCALES[self.scale][1] / 1000
        self.draw_info()

    def adc_init(self):
        self.adc0 = ADC(G0, atten=SCALES[self.scale][0])
        self.vscaling = int((SCALES[self.scale][1] * 1000) / PX_PER_VDIV)

    def roll_mode_init(self, roll, ms_per_div=0):
        if roll:
            if self.roll_mode:
                self.acq_timer.cancel()
                self.clear_buffer()
            self.roll_mode = True
            self.acq_timer = self.periodic(ms_per_div/PX_PER_HDIV, self.acquire_rollmode)
            self.trig = False
        else:
            if not self.roll_mode: return
            self.roll_mode = False
            self.acq_timer.cancel()
            self.start_new_acquisition()



    # Acquisition

    def get_adc(self):
        return self.adc0.read_uv() // self.vscaling

    def acquire_buffer(self, buffer, n, timebase_setting):
        ts_microseconds = TIMEBASES[timebase_setting][0]

        # 1 ms/div requires supa hax
        if timebase_setting == 0:
            x = 0
            for i in range(n):
                buffer[i] = self.source()
                x = i * 0.33 # nop()
            return

        for i in range(n):
            t0 = time.ticks_us()
            buffer[i] = self.source()
            # Improve timebase accuracy by factoring in the acquisition time
            # 50 Hz noise on 20ms/div setting should line up with the grid lines
            t_adc = time.ticks_diff(time.ticks_us(), t0)
            time.sleep_us(ts_microseconds - t_adc)

    def acquire_rollmode(self):
        for i in range(SAMPLES-1):
            self.buffer0[i] = self.buffer0[i+1]
        self.buffer0[SAMPLES-1] = self.source()

        self.roll_display_cnt -= 1
        if self.roll_display_cnt < 0:
            self.after(0, self.draw_samples)
            # For faster rolling timebases, don't draw every new sample
            if TIMEBASES[self.timebase][1] == 2000:
                self.roll_display_cnt = 1
            elif TIMEBASES[self.timebase][1] == 1000:
                self.roll_display_cnt = 3
            else:
                self.roll_display_cnt = 0

    def acquire_async(self):
        self.buffer0[self.bufidx] = self.adc0.read_uv() // self.vscaling
        self.bufidx += 1
        if self.bufidx == SAMPLES:
            self.acq_timer.cancel()
            self.after(0, self.draw_samples)

    def acquisition_start(self):
        # Try to trigger, give up after MAX_TRIGGER_CYCLES
        trig_attempts = 0
        trig_state = 0
        triggered = 0
        trigbuf = bytearray(TRIGGER_WIDTH)
        trig_level = int(self.trig_voltage * 1000000) // self.vscaling
        while trig_attempts < MAX_TRIGGER_CYCLES:
            self.acquire_buffer(trigbuf, TRIGGER_WIDTH, 0)
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

        # If the timebase is too long, get the samples in a timer callback so the UI stays responsive to button presses
        if TIMEBASES[self.timebase][1] >= ACQUIRE_MODE_ASYNC_THRESH:
            self.bufidx = 0
            self.acq_timer = self.periodic(TIMEBASES[self.timebase][0] / 1000, self.acquire_async)
        else:
            self.acquire_buffer(self.buffer0, SAMPLES, self.timebase)
            self.after(0, self.draw_samples)


    # Drawing

    def draw_info(self):
        display.text(font, "{: 4} mV/div".format(SCALES[self.scale][1]), 148, 16, WHITE, GREY)
        if TIMEBASES[self.timebase][1] < 1000:
            display.text(font, "{: 4} ms/div".format(TIMEBASES[self.timebase][1]), 148, 28, WHITE, GREY)
        else:
            display.text(font, "{: 4.0f} s/div ".format(TIMEBASES[self.timebase][1] / 1000), 148, 28, RED, GREY)

        if self.roll_mode:
            trig_msg = "            "
        else:
            trig_msg = "Trig: {:.2f} V".format(self.trig_voltage)
        display.text(font, trig_msg, 140, 40, WHITE, GREY)

        display.text(font, "Src: Pin G0", 148, 52, YELLOW, GREY)
        

    def draw_samples(self):
        if self.should_quit: return

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
            if not self.roll_mode:
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

        # Measurements
        val_max = max(self.buffer0)
        val_min = min(self.buffer0)
        val_avg = sum(self.buffer0) / len(self.buffer0)
        display.text(font, "Min: {:.2f} V".format(self.px_to_volts(val_min)), 140, 76, YELLOW, GREY)
        display.text(font, "Max: {:.2f} V".format(self.px_to_volts(val_max)), 140, 88, YELLOW, GREY)
        display.text(font, "Avg: {:.2f} V".format(self.px_to_volts(val_avg)), 140, 100, YELLOW, GREY)

        self.start_new_acquisition()

main = Oscilloscope