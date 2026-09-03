import numpy as np
from typing import Tuple, List, Dict, Any


def bit_to_samples(raw_bytes: bytes, max_samples: int = None) -> np.ndarray:
    """Unpack raw byte stream into 1-bit boolean array (LSB first: bit 0 is early, bit 7 is late)."""
    if not raw_bytes:
        return np.empty(0, dtype=np.uint8)
    
    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
    # Unpack bits with bitorder='little' (bit 0 first)
    bits = np.unpackbits(arr, bitorder='little')
    if max_samples is not None and len(bits) > max_samples:
        bits = bits[:max_samples]
    return bits


def extract_edges(raw_bytes: bytes, sample_rate: float, max_samples: int = None) -> Tuple[int, np.ndarray, np.ndarray]:
    """
    Extract digital transition edges from raw bit-packed waveform.
    Returns:
        (initial_level, edge_sample_indices, edge_levels)
    """
    samples = bit_to_samples(raw_bytes, max_samples)
    if len(samples) == 0:
        return 0, np.empty(0, dtype=np.int64), np.empty(0, dtype=np.uint8)

    initial_level = int(samples[0])
    diff = np.diff(samples.astype(np.int8))
    transitions = np.where(diff != 0)[0] + 1
    edge_levels = samples[transitions]

    return initial_level, transitions, edge_levels
