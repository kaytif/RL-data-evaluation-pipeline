"""Selected-file reward statistics and concise review context."""
from collections import Counter
from .assessment_summary import one_line


def reward_statistics(results):
    counts = Counter(item.get('score') for item in results)
    total = len(results)
    verified = counts[1] + counts[0]
    return dict(attempts=total, reward_1=counts[1], reward_0=counts[0], unverified=counts[None],
        reward_1_rate=counts[1] / total if total else None,
        reward_0_rate=counts[0] / total if total else None,
        unverified_rate=counts[None] / total if total else None,
        success_rate_verified=counts[1] / verified if verified else None)


def reward_review_payload(blueprint, filename, results):
    categories = {}
    for category in sorted({item.get('category') or 'Unclassified' for item in results}):
        categories[category] = reward_statistics([item for item in results if (item.get('category') or 'Unclassified') == category])
    samples = []
    for score in (1, 0, None):
        for item in [r for r in results if r.get('score') == score][:3]:
            samples.append(dict(reward=score, category=item.get('category'),
                reason_excerpt=one_line(item.get('reasoning'), 500)))
    return dict(source_file=filename, stated_purpose=blueprint.get('required_capabilities', ''),
        statistics=reward_statistics(results), categories=categories, examples=samples,
        example_scope='Up to three reasoning excerpts per outcome. Statistics include every assessed attempt in this selected file.')


def quick_report(blueprint, quality_checks, coverage, statistics=None):
    quality = [c for c in quality_checks if c['phase'] == 'Data quality']
    failures = sum(c['status'] == 'fail' for c in quality)
    unresolved = sum(c['status'] == 'unverified' for c in quality)
    quality_text = f'{failures} failed checks; {unresolved} need review.' if quality else 'Not checked yet.'
    rows = (coverage or {}).get('rows', [])
    if (coverage or {}).get('complete'):
        found = sum(r['Distinct tasks'] for r in rows)
        needed = sum(r['Required tasks'] for r in rows)
        coverage_text = f'{found} observed tasks; {needed} required. '
        coverage_text += '; '.join(f"{r['Category']}: {r['Distinct tasks']} observed / {r['Required tasks']} required"
                                   for r in rows[:4]) + '.'
    else:
        coverage_text = 'Task grouping/counts are not fully established.'
    if statistics and statistics['attempts']:
        reward_text = (f"1: {statistics['reward_1']} ({statistics['reward_1_rate']:.1%}); "
            f"0: {statistics['reward_0']} ({statistics['reward_0_rate']:.1%}); "
            f"unverified: {statistics['unverified']} ({statistics['unverified_rate']:.1%}).")
    else:
        reward_text = 'Not assessed yet; no rewards assigned.'
    return '\n'.join([
        'Purpose: ' + one_line(blueprint.get('required_capabilities') or 'Not specified.', 180),
        'Data quality: ' + quality_text,
        'Task coverage: ' + coverage_text,
        'Rewards: ' + reward_text])
