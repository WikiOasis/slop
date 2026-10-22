from Slop.Module.Module import Module


class Layout(Module):
    name = "layout"
    title = "Preparing farm layout"
    description = "Create the top-level farm directories."

    def run(self) -> None:
        created = 0
        for d in (self.project.root, self.project.versionsDir, self.project.wDir, self.project.scriptsDir):
            if not d.is_dir():
                self.ctx.item("Creating", self.ctx.display(d) + "/")
                created += 1
                if not self.ctx.dryRun:
                    d.mkdir(parents=True, exist_ok=True)
        if not created:
            self.ctx.skip("all directories exist")
