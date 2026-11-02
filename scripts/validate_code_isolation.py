"""Native no-network generated-code validation. Default is a no-action dry run."""
import argparse
import json
from app.code_sandbox import test_patch
from app.storage import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm',action='store_true',help='Copy source/dependencies, probe denials and run tests in disposable AppContainer.')
    args = parser.parse_args()
    if not args.confirm:
        print(json.dumps({'dry_run':True,'production_source_modified':False,'network':False,
            'plan':['Create a disposable source/dependency copy without state or credentials.',
                    'Probe secret/outside-write/network/loopback/child-process denials.',
                    'Run all sandbox-compatible tests; host process-lock test is a separate gate.',
                    'Verify source fingerprints and delete the disposable workspace.']}))
        return
    print(json.dumps(test_patch(ROOT,[],{}),indent=2))


if __name__ == '__main__':
    main()
