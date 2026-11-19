"""Summarize real results only; no synthetic accuracy estimates."""
import argparse
import csv
import json
from pathlib import Path
from statistics import mean


def summarize(rows):
    """Compute summary statistics per experimental mode.

    Token totals/means are computed only over rows where token usage was
    actually available (row['tokens_available'] is True, or the field is
    absent for backward compatibility with older result files that always
    measured tokens). Rows with unavailable usage are counted separately in
    'rows_missing_token_usage' and excluded from 'mean_total_tokens' rather
    than being silently treated as zero-cost. If every row in a mode is
    missing usage, 'mean_total_tokens' is reported as None (unavailable),
    not 0.0, to avoid presenting an incomplete total as a measured zero.
    """
    summary = []
    for mode in ('baseline', 'testing', 'reflexion'):
        r = [x for x in rows if x['mode'] == mode]
        if not r:
            continue
        accepted = [x for x in r if x['internal_pass'] is True]
        false = sum(x['false_accept'] is True for x in accepted)
        token_rows = [x for x in r if x.get('tokens_available', True) and
                      x.get('input_tokens') is not None and x.get('output_tokens') is not None]
        missing_token_rows = len(r) - len(token_rows)
        summary.append({'mode': mode, 'n': len(r), 'solved': sum(x['heldout_pass'] for x in r),
                        'accuracy_pct': round(100*mean(x['heldout_pass'] for x in r), 2),
                        'internal_accepted': len(accepted), 'false_accept_count': false,
                        'false_accept_pct_of_accepted': round(100*false/len(accepted), 2) if accepted else None,
                        'mean_attempts': round(mean(x['attempts'] for x in r), 2),
                        'mean_calls': round(mean(x['calls'] for x in r), 2),
                        'mean_total_tokens': (round(mean(x['input_tokens']+x['output_tokens'] for x in token_rows), 2)
                                              if token_rows else None),
                        'rows_missing_token_usage': missing_token_rows,
                        'mean_solve_seconds': round(mean(x['solve_seconds'] for x in r), 2)})
        if missing_token_rows:
            print(f"WARNING: mode={mode!r} has {missing_token_rows}/{len(r)} row(s) with unavailable "
                  "token usage; mean_total_tokens excludes them and is NOT a complete total.")
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    args = p.parse_args()
    rows = [json.loads(x) for x in (args.directory/'results.jsonl').read_text().splitlines() if x.strip()]
    config = json.loads((args.directory/'config.json').read_text())
    expected = {(task, mode) for task in config['tasks'] for mode in ('baseline','testing','reflexion')}
    pairs = [(r['task_id'], r['mode']) for r in rows]
    if set(pairs) != expected or len(pairs) != len(expected):
        raise ValueError('Incomplete or duplicate results: do not present as a complete experiment.')
    summary = summarize(rows)
    print(json.dumps(summary, indent=2))
    with (args.directory/'summary.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0])); writer.writeheader(); writer.writerows(summary)
    import matplotlib.pyplot as plt
    labels = [r['mode'] for r in summary]
    values = [r['accuracy_pct'] for r in summary]
    fig, ax = plt.subplots(figsize=(7,4))
    bars = ax.bar(labels, values)
    ax.bar_label(bars, labels=[f"{r['solved']}/{r['n']} ({r['accuracy_pct']:.1f}%)" for r in summary], padding=3)
    ax.set_ylim(0, 115); ax.set_ylabel('Final held-out pass rate (%)'); ax.set_title('HumanEval subset: final solution accuracy')
    fig.tight_layout(); fig.savefig(args.directory/'accuracy.png', dpi=180); plt.close(fig)
    fig, ax = plt.subplots(figsize=(7,4))
    token_values = [r['mean_total_tokens'] if r['mean_total_tokens'] is not None else 0 for r in summary]
    bars = ax.bar(labels, token_values)
    bar_labels = [f"{r['mean_total_tokens']:.0f}" if r['mean_total_tokens'] is not None else 'unavailable'
                  for r in summary]
    ax.bar_label(bars, labels=bar_labels, padding=3)
    ax.set_ylabel('Mean input + output tokens (bars labeled "unavailable" have no usage data, not zero cost)')
    ax.set_title('Per-method inference cost proxy')
    fig.tight_layout(); fig.savefig(args.directory/'tokens.png',dpi=180); plt.close(fig)
    by_task = {t:{r['mode']:r for r in rows if r['task_id']==t} for t in config['tasks']}
    print('Paired comparison (Reflexion vs testing):', { 'reflexion_only_pass':sum(v['reflexion']['heldout_pass'] and not v['testing']['heldout_pass'] for v in by_task.values()), 'testing_only_pass':sum(v['testing']['heldout_pass'] and not v['reflexion']['heldout_pass'] for v in by_task.values())})

if __name__ == '__main__':
    main()
