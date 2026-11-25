"""Huecos artificiales: se esconden bloques CONSECUTIVOS de datos reales para medir qué tan bien se reconstruyen."""
import numpy as np


def block_mask(a, lo, hi, lengths, reps, rng):
    """Bloques de cada longitud por estación, dentro de [lo, hi), solo sobre datos observados.
    reps puede ser un entero o una función longitud -> repeticiones (para que los huecos cortos pesen lo mismo
    que los largos en la evaluación y no queden sepultados por ellos)."""
    mask = np.zeros(a.shape, bool)
    for j in range(a.shape[1]):
        for length in lengths:
            for _ in range(reps(length) if callable(reps) else reps):
                for _try in range(80):
                    if hi - lo <= length + 2:
                        break
                    s = int(rng.integers(lo, hi - length + 1))
                    if np.isfinite(a[s:s + length, j]).all() and not mask[s:s + length, j].any():
                        mask[s:s + length, j] = True
                        break
    return mask


def coverage_mask(a, lo, hi, lengths, coverage, rng):
    """Bloques de longitud aleatoria hasta cubrir una fracción del periodo (se usa para entrenar)."""
    mask = np.zeros(a.shape, bool)
    target = coverage * (hi - lo)
    for j in range(a.shape[1]):
        covered, tries = 0, 0
        while covered < target and tries < 600:
            tries += 1
            length = int(rng.choice(lengths))
            if hi - lo <= length + 2:
                continue
            s = int(rng.integers(lo, hi - length + 1))
            if np.isfinite(a[s:s + length, j]).all() and not mask[s:s + length, j].any():
                mask[s:s + length, j] = True
                covered += length
    return mask
