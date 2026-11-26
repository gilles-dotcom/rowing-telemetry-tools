#include "ant_power_encoder.h"
#include <string.h>

void ant_power_init_channel(AntPowerChannelState* state) {
    memset(state, 0, sizeof(AntPowerChannelState));
}

void ant_power_update_stroke(AntPowerChannelState* state, float watts, float spm) {
    if (watts < 0.0f) watts = 0.0f;
    if (spm < 0.0f) spm = 0.0f;

    uint16_t w = (uint16_t)(watts + 0.5f);
    uint8_t c = (spm > 255.0f) ? 255 : (uint8_t)(spm + 0.5f);

    state->event_count++;
    state->accumulated_power_w += w;
    state->current_power_w = w;
    state->cadence_spm = c;
}

void ant_power_format_page10(const AntPowerChannelState* state, uint8_t packet[8]) {
    packet[0] = ANT_POWER_PAGE_STANDARD;
    packet[1] = state->event_count;
    packet[2] = 0xFF; // Pedal differentiation not used
    packet[3] = state->cadence_spm;
    packet[4] = (uint8_t)(state->accumulated_power_w & 0xFF);
    packet[5] = (uint8_t)((state->accumulated_power_w >> 8) & 0xFF);
    packet[6] = (uint8_t)(state->current_power_w & 0xFF);
    packet[7] = (uint8_t)((state->current_power_w >> 8) & 0xFF);
}
