class Duration:
    @staticmethod
    def format(seconds: float) -> str:
        if seconds < 1:
            return f"{seconds * 1000:.0f}ms"
        if seconds < 60:
            return f"{seconds:.3f}s"
        minutes, rest = divmod(seconds, 60)
        if minutes < 60:
            return f"{int(minutes)}m {rest:06.3f}s"
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours}h {minutes:02d}m {int(rest):02d}s"

    @staticmethod
    def live(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, rest = divmod(seconds, 60)
        return f"{int(minutes)}m {rest:04.1f}s"
