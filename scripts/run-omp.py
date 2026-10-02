"""Keep an installed OMP session connected; store all raw output externally."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--omp', required=True)
    parser.add_argument('--agent-dir', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[1]
    agent = args.agent_dir.resolve()
    workspace = args.workspace.resolve()
    for path in (agent, workspace):
        if path == checkout or checkout in path.parents:
            raise SystemExit('Use an external OMP profile and workspace.')
    if Path(os.environ.get('PI_CODING_AGENT_DIR', '')).resolve() != agent:
        raise SystemExit('OMP profile does not match the launcher environment.')
    if not (agent/'telegram.json').is_file():
        raise SystemExit('Prepare the dedicated Telegram configuration first.')
    workspace.mkdir(parents=True, exist_ok=True)
    logs = agent/'logs'
    logs.mkdir(exist_ok=True)
    started = time.time()
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
    child = subprocess.Popen(
        [args.omp, '--mode', 'rpc', '--no-ui', '--no-title', '--cwd', str(workspace),
         '--extension', str(checkout/'src/omp-extension/activity.mjs')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace', bufsize=1)

    def record(stream, suffix):
        # Never echo model replies, credentials, identities or errors publicly.
        with (logs/('worklink-omp-' + stamp + suffix)).open('w', encoding='utf-8') as target:
            for line in stream:
                target.write(line)
                target.flush()

    threads = [threading.Thread(target=record, args=(stream, suffix), daemon=True)
               for stream, suffix in ((child.stdout, '-stdout.jsonl'), (child.stderr, '-stderr.log'))]
    for thread in threads:
        thread.start()
    ready = False
    try:
        child.stdin.write(json.dumps({'type': 'prompt', 'message': '/telegram-connect'}) + '\n')
        child.stdin.flush()
        while child.poll() is None:
            if not ready:
                try:
                    state = json.loads((agent/'tmp/telegram/state.json').read_text(encoding='utf-8'))
                    ready = state.get('writtenAtMs', 0) >= started * 1000 and state.get('runtime', {}).get('pollingActive') is True
                except (OSError, ValueError):
                    pass
                if ready:
                    print('OMP Telegram polling is active. Use /chat in Teams. Ctrl+C stops this session.', flush=True)
                elif time.time() - started > 45:
                    raise SystemExit('OMP did not connect. Inspect the external private OMP logs.')
            time.sleep(0.5)
        raise SystemExit('OMP stopped. Inspect the external private OMP logs.')
    except KeyboardInterrupt:
        print('Stopping the dedicated OMP session.', flush=True)
    finally:
        if child.poll() is None:
            try:
                child.stdin.write(json.dumps({'type': 'prompt', 'message': '/telegram-disconnect'}) + '\n')
                child.stdin.flush()
                time.sleep(1)
            except (OSError, BrokenPipeError):
                pass
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for thread in threads:
            thread.join(timeout=1)


if __name__ == '__main__':
    main()
