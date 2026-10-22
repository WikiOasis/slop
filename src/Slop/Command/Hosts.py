import argparse

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Config.Inventory import Inventory
from Slop.Output.Console import Console


class Hosts(Command):
    name = "hosts"
    help = "list the inventory: roles, their hosts and services"
    forward = False

    def run(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        inventory = config.inventory
        console.write(f"<comment>Inventory</comment> <dim>deploy master: {inventory.master or 'this host'}</dim>")
        for role in inventory.roles.values():
            projects = [p.name for p in config.projects.values() if role.name in p.deploy.roles]
            extras = []
            if role.batch > 1:
                extras.append(f"batch {role.batch}")
            if role.soak:
                extras.append(f"soak {role.soak:g}s")
            if role.services:
                extras.append("services " + ", ".join(role.services))
            if projects:
                extras.append("projects " + ", ".join(projects))
            console.write(f"  <info>{role.name}</info> <dim>{' · '.join(extras)}</dim>")
            for host in role.hosts:
                console.write(f"    {Inventory.short(host)} <dim>{host}</dim>")
