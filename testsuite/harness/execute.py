import os
import runpy
import sys
import traceback

# Stay independent of the harness so broken imports are reported as errors
def main():
    try:
        script = sys.argv[1]
        sys.argv = sys.argv[1:]
        sys.path[0] = os.path.dirname(os.path.abspath(script))
        runpy.run_path(script, run_name='__main__')
    except SystemExit as error:
        if error.code is None or isinstance(error.code, int) and error.code in (0, 1, 2, 77, 78):
            raise
        try:
            print('invalid test exit status', file=sys.stderr)
        finally:
            raise SystemExit(2)
    except BaseException as error:
        try:
            traceback.print_exc()
        finally:
            raise SystemExit(1 if isinstance(error, AssertionError) else 2)

if __name__ == '__main__':
    main()
