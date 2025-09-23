# Agent Guidelines for Tron ESP32-S3 Controller

This project targets an ESP32-S3 running MicroPython to drive a WS2811-based LED strip. When contributing changes, remember:

- Keep MicroPython limitations in mind – every additional allocation, Python loop, or blocking call directly impacts frame timing on the microcontroller.
- Prioritise the animation loop above all else. The prime directive is *speed and smoothness* of the Tron burst animation.
  - Avoid work inside the animation hot path that doesn’t directly contribute to rendering.
  - Batch operations so we write to the strip only once per visual frame.
  - Prefer integer math, reuse buffers, and eliminate redundant calculations where possible.
  - 
- Always test with multiple overlapping bursts. A change that looks fine for one burst but causes stutter with two or three should not land.
- Treat timing constants (`DELAY_MIN`, `DELAY_MAX`, `BURST_GAP_MS`, etc.) as milliseconds unless clearly documented otherwise.

## Animation Loop Requirements

1. Use the central scheduler (`animation_consumer`) to manage deadlines for every active burst. Do not reintroduce per-burst blocking loops.
2. Render once per scheduler tick, compositing all active bursts before calling `np.write()`.
3. Keep the burst state compact (position, direction, endpoint, trail, speed, delay, `next_step_at`) and avoid per-step dynamic allocation.
4. When adding new features (colour effects, easing, etc.), measure the impact on total frame time and ensure smoothness under multiple simultaneous bursts.

Following these guidelines keeps the animation responsive and visually smooth on the ESP32-S3.
