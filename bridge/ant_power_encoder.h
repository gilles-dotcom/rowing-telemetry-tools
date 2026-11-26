#ifndef ANT_POWER_ENCODER_H
#define ANT_POWER_ENCODER_H

#include <stdint.h>

#define ANT_POWER_PAGE_STANDARD 0x10

typedef struct {
    uint8_t event_count;
    uint16_t accumulated_power_w;
    uint16_t current_power_w;
    uint8_t cadence_spm;
} AntPowerChannelState;

void ant_power_init_channel(AntPowerChannelState* state);
void ant_power_update_stroke(AntPowerChannelState* state, float watts, float spm);
void ant_power_format_page10(const AntPowerChannelState* state, uint8_t packet[8]);

#endif /* ANT_POWER_ENCODER_H */
