"""Run with python -m novelvideo.technique_library.cli."""
import argparse
import json
import sys

from .ingest import IngestError, import_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description='Import pinned H3 source metadata offline')
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report')
    args = parser.parse_args()
    try:
        bundle = import_catalog(args.source_root, args.output, args.report)
    except (IngestError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'report': getattr(exc, 'report', {})}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, 'report': bundle['report']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
