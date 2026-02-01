import argparse
import os
import subprocess
import sys
import tempfile
import timeit
from dataclasses import dataclass
from pathlib import Path

tests_folder_path = Path(__file__).parent
"""The path to the `tests` directory (parent of the current script)."""

all_tests = list(sorted(map(
    lambda p: p.parent.name,  # Get only the folder name, e.g., `01-simple`
    tests_folder_path.glob('*/input.qmd')
)))
"""The list of the tests folders (sub-directories that contain an `input.md`)."""


@dataclass
class TestResult:
    """The result of a single test."""
    name: str
    success: bool
    return_code: int
    input_file_path: Path
    actual_output: list    # Filtered result to remove Quarto details
    expected_output: list
    actual_error: list     # Filtered errors to remove Quarto details
    expected_error: list
    execution_time_ms: float


def green(msg):
    """Put a message in green. (Only when ANSI codes are supported.)"""
    return f'\033[92m{msg}\033[0m'


def red(msg):
    """Put a message in red. (Only when ANSI codes are supported.)"""
    return f'\033[91m{msg}\033[0m'


def bold(msg):
    """Put a message in bold. (Only when ANSI codes are supported.)"""
    return f'\033[1m{msg}\033[0m'


def ensure_symlink_exists():
    """Create the symlink to the `_extensions` folder if it does not exist."""
    # We must check first or catch FileExistsError, because `os.symlink`
    # raises an error if the target already exists. Note that we do not check
    # if the target actually is a symlink and points to the expected folder.
    # (We could replace it with a copy or a hardlink)
    if not tests_folder_path.joinpath('_extensions').exists():
        os.symlink(
            src=tests_folder_path.joinpath('../_extensions').resolve(),
            dst=tests_folder_path.joinpath('_extensions').resolve(),
            target_is_directory=True
        )


def read_file(file_path, default=None):
    """
    Read and return a file's contents, or optionally a default value.

    If the file can not be found:
        - if `default` is set, ignores the error, and returns this value.
        - else, raises the error.
    If any other error happens, raises it.
    """
    try:
        with open(file_path, 'r') as f:
            # `readlines` would not return the exact same result with blank lines
            lines = f.read()
            return lines
    except FileNotFoundError as e:
        if default is not None:
            return default
        raise e


def test_single_dir(dir_name: str):
    """Run a single test (directory) and return its result."""
    # Path to this test folder
    test_path = tests_folder_path / dir_name
    # Path to the input file (Quarto document).
    input_file_path = test_path / 'input.qmd'
    # Path to output file (same as input, but with `.md` extension).
    output_file_path = test_path / 'input.md'

    # Create a new process and ask Quarto to render the document.
    # We also measure the wall-clock time (in miliseconds).
    start_time = timeit.default_timer()
    command = subprocess.run(
        [
            'pandoc',
            '--lua-filter',
            '_extensions/acronyms/parse-acronyms.lua',
            '-i',
            input_file_path,
            '-r',
            'markdown',
            '-w',
            'markdown',
            # '--verbose'
        ],
        # We want to capture stdout and stderr
        capture_output=True,
        # And we want them to be decoded as text rather than bytes
        text=True,
    )
    end_time = timeit.default_timer()
    # Get return code, standard error (errors or warnings log).
    code, stdout, stderr = command.returncode, command.stdout, command.stderr
    # The expected output document
    expected_output = read_file(test_path / 'expected.md', default='')
    # The expected errors / warnings log
    expected_error = read_file(test_path / 'expected.stderr', default='')

    print('\n\nSTDERR:')
    print(stderr)

    import json
    print('\n\nSTDOUT:')
    print(json.dumps(stdout, indent=4))
    print('\n\nEXPECTED:')
    print(json.dumps(expected_output, indent=4))

    success = (code == 0) and\
              (stdout == expected_output) and\
              (stderr == expected_error)

    return TestResult(
        dir_name,
        success,
        code,
        input_file_path,
        stdout,
        expected_output,
        stderr,
        expected_error,
        end_time - start_time
    )


def reporter_oneline(result):
    """Report a single result on a single line."""
    success = bold(green('PASS')) if result.success else bold(red('FAIL'))
    ok, ko = bold(green('OK')), bold(red('KO'))
    code = ok if result.return_code == 0 else bold(red(str(result.return_code)))
    stdout = ok if result.actual_output == result.expected_output else ko
    stderr = ok if result.actual_error == result.expected_error else ko
    return f' {success} (Retcode: {code} | Stdout: {stdout} | Stderr: {stderr})'


def indent(text):
    """Helper for the Markdown reporter, indents all lines in a text."""
    if isinstance(text, str):
        text = text.split('\n')
    text = map(lambda s: '    ' + s, text)
    return '\n'.join(text)


def reporter_markdown(results):
    """Report all results to a Markdown summary file."""
    md = "# Automatic tests report\n\n"

    # 1st section: summary table (short, one-line for each test)
    md += "## Summary\n\n"
    md += "| Test | Result | :alarm_clock: Duration (s) |\n"
    for result in results:
        result_emoji = ':white_check_mark: PASS' if result.success else ':x: FAIL'
        exec_time = result.execution_time_ms / 1_000
        heading_slug = result.name.replace(' ', '-')
        md += f'| [{result.name}](#{heading_slug}) | {result_emoji} | {exec_time:.0f} |\n'
    md += '\n'

    # 2nd section: current quarto installation (versions, dependencies, ...)
    # Useful to debug, when there is a problem with a test, which version of
    # quarto or pandoc is used, and whether this version is causing the problem.
    # However, the default output contains control characters (e.g., `\r`), so
    # we need to output the log instead to a file, and then read the file...
    fd, quarto_check_output_path = tempfile.mkstemp()
    command = subprocess.run(
        ['quarto', 'check', '--quiet', '--log-format', 'plain', '--log', quarto_check_output_path]
    )
    quarto_check_output = read_file(quarto_check_output_path)
    os.close(fd)
    os.unlink(quarto_check_output_path)
    md += '## Configuration details\n\n'
    md += f'```{'\n'.join(quarto_check_output)}```\n\n'

    # 3rd section: detailed results for each test
    # At some point, `inspect.cleandoc` could be useful to avoid writing
    # unidented strings (less readable).
    for result in results:
        # Result header
        md += f'## {result.name}\n\n'

        # Return code == 0?
        if result.return_code == 0:
            md += ':white_check_mark: Return code was <span style="color: green">0</span>\n\n'
        else:
            md += f':x: Return code was <span style="color: red">{result.return_code}</span>\n\n'

        # Output == expected output?
        if result.actual_output == result.expected_output:
            md += ':white_check_mark: Output corresponds to expected output:\n'
        else:
            md += ':x: Output does not correspond to expected output:\n'
        md += f'''
<details>
<summary>Actual:</summary>

{indent(result.actual_output)}

</details>

<details>
<summary>Expected:</summary>

{indent(result.expected_output)}

</details>

'''

        # Error == expected error?
        if result.actual_error == result.expected_error:
            md += ':white_check_mark: Error corresponds to expected error:\n'
        else:
            md += ':x: Error does not correspond to expected error:\n'
        md += f'''
<details>
<summary>Actual:</summary>

{indent(result.actual_error)}

</details>

<details>
<summary>Expected:</summary>

{indent(result.expected_error)}

</details>

'''

        # The input file, for full details; this includes the YAML metadata
        test_input = read_file(result.input_file_path)
        md += f'''

:page_with_curl: Details:

<details>
<summary>Input file:</summary>

{indent(test_input)}

</details>

<details>
<summary>Original stderr</summary>

{indent(result.original_error)}

</details>


'''

    return md


def main(tests_to_perform) -> int:
    # Quarto needs to access the `_extensions` folder that is in the project
    # root (the parent folder). We thus ensure that a symbolic link exists.
    ensure_symlink_exists()

    # If tests were specified in args, run only them.
    # Otherwise, run all tests in the tests/ folder.
    if len(tests_to_perform) == 0:
        tests_to_perform = all_tests

    # Run tests, save results, and output regularly to stdout.
    # We want a short report (1 line by test).
    results = []
    nb_successes, nb_fails = 0, 0
    for test in tests_to_perform:
        # Print a message to keep the user waiting
        print('Running test', bold(test), '...', end='', flush=True)
        result = test_single_dir(test)
        results.append(result)
        if result.success:
            nb_successes += 1
        else:
            nb_fails += 1
        print(reporter_oneline(result))

    # Print short summary
    if nb_fails == 0:
        # Not a single fail, put `nb_successes`` in bold and green to highlight
        nb_successes = green(bold(nb_successes))
    else:
        # Some fails, put `nb_fails` in bold and red to highlight
        nb_fails = red(bold(nb_fails))
    print(f'\nTotal: {nb_successes} PASS / {nb_fails} FAIL')

    # If we are in a GitHub CI, we also want to write a "Job Summary",
    # in the form of a (detailed) Markdown report.
    github_summary_path = os.getenv('GITHUB_STEP_SUMMARY')
    if github_summary_path is not None:
        with open(github_summary_path, 'w') as f:
            f.write(reporter_markdown(results))

    return nb_fails


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Helper script to run the test suite for acronyms.'
    )
    parser.add_argument(
        'folders',
        metavar='test_name',
        nargs='*',
        help='One (or several) specific tests to run. By default, runs all tests.'
    )
    args = parser.parse_args()
    retcode = main(args.folders)
    sys.exit(retcode)
