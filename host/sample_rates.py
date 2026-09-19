"""Shared playback/DAC clock contract. Existing files and APIs default to 200 MHz."""

DEFAULT_SAMPLE_HZ = 200_000_000
SAMPLE_RATES = (100_000_000, 125_000_000, 200_000_000)


def validate_sample_hz(value):
    if type(value) is not int or value not in SAMPLE_RATES:
        raise ValueError('输出采样率须为 100、125 或 200 MSPS')
    return value


def sample_period_ns(value):
    return 1e9 / validate_sample_hz(value)


def sample_rate_label(value):
    return f'{validate_sample_hz(value)/1e6:g} MSPS · {sample_period_ns(value):g} ns/点'


def supports_sample_clock(caps):
    return caps.core_hz in SAMPLE_RATES and caps.dac_divider == 1 and caps.max_runs > 0
