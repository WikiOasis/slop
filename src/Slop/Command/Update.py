import argparse

from Slop.Command.Command import Command
from Slop.Output.Console import Console
from Slop.Project.ProjectLoader import ProjectLoader
from Slop.Runner import Runner
from Slop.SlopError import SlopError


class Update(Command):
    name = "update"
    help = "rebuild one part of a project without deploying: config, core, extensions, composer, ..."

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        sections = []
        for projectType in ProjectLoader.all().values():
            lines = "\n".join(f"  {what:<11} {desc}" for what, (_, _, desc) in projectType.TARGETS.items())
            sections.append(f"what to update, for {projectType.type} projects:\n{lines}")
        parser.description = (
            self.help
            + "\n\n"
            + "\n\n".join(sections)
            + "\n\nexamples:\n  slop update core\n  slop update extensions CheckUser Vector\n"
            "  slop update composer -m 'bump sentry'\n  slop update -p landing code"
        )
        parser.add_argument("what", metavar="WHAT", help="what to update (see above)")
        parser.add_argument("names", nargs="*", metavar="NAME", help="limit to these (e.g. extension names)")
        parser.add_argument("-p", "--project", help="project to update (default: slop.yaml's default)")
        parser.add_argument("--no-pull", dest="noPull", action="store_true", help="don't pull the config repo first")
        Runner.addOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        runner = Runner(args, console)
        runner.syncConfig()
        (project,) = runner.projects([args.project] if args.project else runner.config.defaults[:1])

        what = project.ALIASES.get(args.what, args.what)
        if what not in project.TARGETS:
            raise SlopError(
                f"{project.type} projects can't update {args.what!r}; try one of: {', '.join(project.TARGETS)}"
            )
        modules, takesNames, _ = project.TARGETS[what]
        if args.names and not takesNames:
            raise SlopError(f"`slop update {what}` doesn't take names")

        pipeline = modules or project.stage("build")
        scope = project.scope(what, args.names)
        explicit = set(modules) if modules and len(modules) == 1 else set()
        label = f"update {what}" + (f" {' '.join(args.names)}" if args.names else "")
        runner.run(label, [(project, pipeline, scope, explicit)])
