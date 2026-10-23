import argparse
import json
from .summary import build_summary


def main():
    parser=argparse.ArgumentParser(description='Tony portfolio demo: deterministic synthetic restaurant reporting.')
    parser.add_argument('command',choices=['summary'],nargs='?',default='summary')
    parser.parse_args()
    print(json.dumps(build_summary(),indent=2))


if __name__=='__main__':main()
