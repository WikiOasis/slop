import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOTS = ["src", "tests", "scripts"]
YAML = re.compile(r"^\s*#")


def pythonProblems(path: Path) -> list[str]:
    source = path.read_text()
    problems = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and not (token.start[0] == 1 and token.string.startswith("#!")):
            problems.append(f"{path}:{token.start[0]}: comment")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                problems.append(f"{path}:{first.lineno}: docstring")
    return problems


def yamlProblems(path: Path) -> list[str]:
    return [f"{path}:{i}: comment" for i, line in enumerate(path.read_text().splitlines(), 1) if YAML.match(line)]


def main(base: Path) -> int:
    problems = []
    for root in ROOTS:
        for path in sorted((base / root).rglob("*.py")):
            problems += pythonProblems(path)
    for pattern in ("*.yml", "*.yaml"):
        for path in sorted(base.glob(f".github/**/{pattern}")):
            problems += yamlProblems(path)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} comment(s) found; slop's code explains itself through names instead", file=sys.stderr)
        return 1
    print("no comments found")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()))
