from Slop.SlopError import SlopError


class DeployFailure(SlopError):
    def __init__(self, host: str, step: str, error: str, touched: set[str]):
        error = error.removeprefix(f"{host}: ")
        super().__init__(f"{host}: {step} failed: {error}")
        self.host = host
        self.step = step
        self.error = error
        self.touched = touched
