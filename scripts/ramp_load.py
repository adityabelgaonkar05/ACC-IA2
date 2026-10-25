#!/usr/bin/env python3
"""One-minute, bounded HTTP ramp. Never creates or scales containers itself."""
import argparse
import asyncio
import time
from collections import Counter

import httpx

GATEWAY = 'http://127.0.0.1:8000'
MANAGEMENT = 'http://127.0.0.1:8001'
RATES = (1, 2, 3, 4, 6)


def rate_at(elapsed, duration):
    return RATES[min(len(RATES) - 1, int(elapsed / (duration / len(RATES))))]


def check_ready(status):
    app = status.get('application', {})
    count = len(status.get('group', {}).get('Instances', []))
    if app.get('kind') != 'api' or app.get('load_path') != '/work':
        raise ValueError(
            f"Active app: {app.get('name', 'unknown')} ({app.get('kind', 'unknown')}), "
            f"release {status.get('current_tag', 'unknown')}.\n"
            'The ramp requires a stateless CPU API exposing /work. In http://localhost:5173:\n'
            'Applications → enter your CPU API source path and startup command → set a new version\n'
            '→ confirm 0.25 CPU → Build & deploy API → wait for Successful, then rerun this command.'
        )
    if app.get('cpu_limit', 1) != .25:
        raise ValueError('Build your CPU API with a 0.25 CPU quota per replica. Use a new version tag.')
    if count != 1:
        raise ValueError(f'Expected 1 initial replica, found {count}. Stop other load and wait for scale-in, or restart the app.')
    if not status.get('verified'):
        raise ValueError('Wait for the deployment to be Successful and healthy before recording.')
    if status.get('group', {}).get('MaxSize') != 5:
        raise ValueError('Restart ./scripts/local.sh to load the new maximum of five replicas.')


async def run(duration):
    counters = Counter()
    seen = set()
    pending = set()
    async with httpx.AsyncClient(timeout=4, trust_env=False,
                                 limits=httpx.Limits(max_connections=28)) as client:
        response = await client.get(MANAGEMENT + '/api/status')
        response.raise_for_status()
        check_ready(response.json())
        response = await client.get(MANAGEMENT + '/api/load')
        response.raise_for_status()
        if response.json().get('status') == 'running':
            raise ValueError('Cancel the dashboard load test before running this ramp.')
        print(f'Ramping for {duration}s: 1 → 2 → 3 → 4 → 6 requests/sec, one stage every {duration/5:g}s.', flush=True)
        print('Watch Scaling & monitoring. Ctrl+C stops load. Only real CPU can trigger scaling.', flush=True)
        start = time.monotonic()
        peak = 1

        async def request():
            try:
                r = await client.get(GATEWAY + '/work', params={'seconds': 1})
                r.raise_for_status()
                replica = r.json().get('replica')
                if replica:
                    seen.add(replica)
                counters['ok'] += 1
            except (httpx.HTTPError, ValueError):
                counters['errors'] += 1

        async def observe():
            nonlocal peak
            while time.monotonic() - start < duration:
                try:
                    r = await client.get(MANAGEMENT + '/api/status')
                    r.raise_for_status()
                    status = r.json()
                    count = len(status['group']['Instances'])
                    peak = max(peak, count)
                    elapsed = time.monotonic() - start
                    print(f'{elapsed:4.0f}s | target {rate_at(elapsed,duration)} req/s | '
                          f'replicas {count}/5 | OK {counters["ok"]} | errors {counters["errors"]}', flush=True)
                    if status.get('monitor_error'):
                        print('  Monitor error: ' + status['monitor_error'], flush=True)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    print(f'  Status unavailable: {exc}', flush=True)
                await asyncio.sleep(2)

        observer = asyncio.create_task(observe())
        try:
            next_request = start
            while time.monotonic() - start < duration:
                elapsed = time.monotonic() - start
                if len(pending) < 24:
                    task = asyncio.create_task(request())
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                else:
                    counters['skipped'] += 1
                next_request += 1 / rate_at(elapsed, duration)
                await asyncio.sleep(max(0, next_request - time.monotonic()))
            if pending:
                await asyncio.gather(*list(pending))
        finally:
            observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
            for task in list(pending):
                task.cancel()
            await asyncio.gather(*list(pending), return_exceptions=True)
        print(f'Load stopped. Peak observed: {peak}/5 replicas; '
              f'{len(seen)} replica identities served traffic; '
              f'{counters["ok"]} OK, {counters["errors"]} errors, {counters["skipped"]} skipped.', flush=True)
        if peak < 5:
            print('Five replicas were not observed. Check real CPU/error charts; try --duration 90. '
                  'Allocate at least 2 CPUs to Docker and close competing CPU-heavy apps.', flush=True)
        print('Keep the dashboard open to show automatic scale-in after load stops.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=60, choices=range(30, 181), metavar='30..180')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.duration))
    except KeyboardInterrupt:
        print('\nLoad cancelled; no more requests will be sent.')
    except (ValueError, httpx.HTTPError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
