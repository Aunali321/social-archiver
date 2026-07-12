def retry_wait(attempt: int, base: float = 5, factor: float = 3, cap: float = 60) -> float:
    return min(base * (factor ** (attempt - 1)), cap)
