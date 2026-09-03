import numpy as np
from typing import List, Dict, Any, Optional
from .edge import bit_to_samples, extract_edges


def decode_uart(
    raw_bytes: bytes,
    sample_rate: float,
    baud_rate: int = 115200,
    data_bits: int = 8,
    parity: str = "none", # "none", "even", "odd"
    stop_bits: float = 1.0,
    max_samples: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Software UART bit-stream decoder.
    Extracts frames with timestamps, data bytes, hex strings, parity/framing errors.
    """
    samples = bit_to_samples(raw_bytes, max_samples)
    if len(samples) == 0:
        return []

    bit_samples = sample_rate / float(baud_rate)
    half_bit = bit_samples / 2.0

    frames = []
    idx = 0
    n = len(samples)

    while idx < n - int(bit_samples * (data_bits + 2)):
        # Look for start bit (falling edge from 1 -> 0)
        if samples[idx] == 1 and samples[idx + 1] == 0:
            start_sample = idx + 1
            # Sample at middle of start bit
            sample_pt = start_sample + bit_samples * 0.5
            if int(sample_pt) >= n or samples[int(sample_pt)] != 0:
                idx += 1
                continue

            # Read data bits (LSB first)
            byte_val = 0
            bit_error = False
            for b in range(data_bits):
                pt = int(start_sample + (b + 1.5) * bit_samples)
                if pt >= n:
                    bit_error = True
                    break
                bit = samples[pt]
                byte_val |= (bit << b)

            if bit_error:
                break

            # Read parity if configured
            has_parity_err = False
            next_bit_idx = data_bits + 1.5
            if parity != "none":
                pt = int(start_sample + next_bit_idx * bit_samples)
                if pt < n:
                    p_bit = samples[pt]
                    ones = bin(byte_val).count('1')
                    if parity == "even" and (ones % 2 != p_bit):
                        has_parity_err = True
                    elif parity == "odd" and (ones % 2 == p_bit):
                        has_parity_err = True
                next_bit_idx += 1.0

            # Read stop bit (must be 1)
            pt_stop = int(start_sample + next_bit_idx * bit_samples)
            framing_err = False
            if pt_stop < n and samples[pt_stop] == 0:
                framing_err = True

            frames.append({
                "sample_index": start_sample,
                "timestamp_s": start_sample / sample_rate,
                "data": byte_val,
                "char": chr(byte_val) if 32 <= byte_val <= 126 else ".",
                "hex": f"0x{byte_val:02X}",
                "parity_error": has_parity_err,
                "framing_error": framing_err
            })

            # Advance past stop bit
            idx = int(start_sample + (next_bit_idx + 0.5) * bit_samples)
        else:
            idx += 1

    return frames


def decode_i2c(
    scl_bytes: bytes,
    sda_bytes: bytes,
    sample_rate: float,
    max_samples: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Software I2C protocol decoder.
    Extracts START, STOP, Address (R/W), Data bytes, ACK/NACK.
    """
    scl = bit_to_samples(scl_bytes, max_samples)
    sda = bit_to_samples(sda_bytes, max_samples)
    min_len = min(len(scl), len(sda))
    if min_len == 0:
        return []

    events = []
    in_transfer = False
    current_byte = 0
    bit_count = 0
    is_addr_byte = True

    for i in range(1, min_len):
        scl_prev, scl_curr = scl[i - 1], scl[i]
        sda_prev, sda_curr = sda[i - 1], sda[i]

        # START condition: SDA falls while SCL is High
        if scl_curr == 1 and sda_prev == 1 and sda_curr == 0:
            events.append({"type": "START", "sample_index": i, "timestamp_s": i / sample_rate})
            in_transfer = True
            current_byte = 0
            bit_count = 0
            is_addr_byte = True
            continue

        # STOP condition: SDA rises while SCL is High
        if scl_curr == 1 and sda_prev == 0 and sda_curr == 1:
            events.append({"type": "STOP", "sample_index": i, "timestamp_s": i / sample_rate})
            in_transfer = False
            continue

        # Sample data on SCL rising edge
        if in_transfer and scl_prev == 0 and scl_curr == 1:
            bit_val = int(sda_curr)
            if bit_count < 8:
                current_byte = (current_byte << 1) | bit_val
                bit_count += 1
            else:
                # 9th bit: ACK (0) or NACK (1)
                ack = (bit_val == 0)
                if is_addr_byte:
                    addr = (current_byte >> 1) & 0x7F
                    is_read = (current_byte & 1) == 1
                    events.append({
                        "type": "ADDRESS",
                        "address": addr,
                        "hex": f"0x{addr:02X}",
                        "is_read": is_read,
                        "ack": ack,
                        "sample_index": i,
                        "timestamp_s": i / sample_rate
                    })
                    is_addr_byte = False
                else:
                    events.append({
                        "type": "DATA",
                        "data": current_byte,
                        "hex": f"0x{current_byte:02X}",
                        "ack": ack,
                        "sample_index": i,
                        "timestamp_s": i / sample_rate
                    })
                current_byte = 0
                bit_count = 0

    return events


def decode_spi(
    clk_bytes: bytes,
    mosi_bytes: bytes,
    miso_bytes: Optional[bytes] = None,
    cs_bytes: Optional[bytes] = None,
    sample_rate: float = 1e6,
    cpol: int = 0,
    cpha: int = 0,
    bits_per_word: int = 8,
    max_samples: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Software SPI protocol decoder supporting Mode 0, 1, 2, 3.
    """
    clk = bit_to_samples(clk_bytes, max_samples)
    mosi = bit_to_samples(mosi_bytes, max_samples)
    has_miso = miso_bytes is not None
    miso = bit_to_samples(miso_bytes, max_samples) if has_miso else None
    has_cs = cs_bytes is not None
    cs = bit_to_samples(cs_bytes, max_samples) if has_cs else None

    min_len = min(len(clk), len(mosi))
    if has_miso: min_len = min(min_len, len(miso))
    if has_cs: min_len = min(min_len, len(cs))

    if min_len == 0:
        return []

    # Sampling edge determination
    # CPOL=0, CPHA=0 -> rising edge
    # CPOL=0, CPHA=1 -> falling edge
    # CPOL=1, CPHA=0 -> falling edge
    # CPOL=1, CPHA=1 -> rising edge
    sample_on_rising = (cpol == cpha)

    transfers = []
    mosi_word = 0
    miso_word = 0
    bit_count = 0

    for i in range(1, min_len):
        if has_cs and cs[i] == 1:
            # CS inactive (High)
            bit_count = 0
            mosi_word = 0
            miso_word = 0
            continue

        clk_prev, clk_curr = clk[i - 1], clk[i]
        edge_match = (clk_prev == 0 and clk_curr == 1) if sample_on_rising else (clk_prev == 1 and clk_curr == 0)

        if edge_match:
            mosi_word = (mosi_word << 1) | int(mosi[i])
            if has_miso:
                miso_word = (miso_word << 1) | int(miso[i])
            bit_count += 1

            if bit_count == bits_per_word:
                record = {
                    "sample_index": i,
                    "timestamp_s": i / sample_rate,
                    "mosi": mosi_word,
                    "mosi_hex": f"0x{mosi_word:02X}",
                }
                if has_miso:
                    record["miso"] = miso_word
                    record["miso_hex"] = f"0x{miso_word:02X}"
                transfers.append(record)

                mosi_word = 0
                miso_word = 0
                bit_count = 0

    return transfers
