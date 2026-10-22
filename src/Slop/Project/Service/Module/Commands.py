from Slop.Module.Module import Module


class Commands(Module):
    name = "commands"
    title = "Running build commands"
    description = "Run the project's build: commands (composer install, npm ci, ...) in the checkout."

    def run(self) -> None:
        if not self.project.commands:
            self.ctx.skip("no build commands")
            return
        for command in self.project.commands:
            self.ctx.item("Running", command)
            self.ctx.stream(
                ["bash", "-euo", "pipefail", "-c", command],
                lambda line: self.ctx.event(10, line),
                cwd=self.project.root,
            )
