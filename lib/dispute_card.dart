import 'package:flutter/material.dart';

/// Shows what the photo check found. The model only RECOMMENDS: a person (the business or Konsyer) decides.
/// Used on the customer's Held payments, the business dashboard and the admin console.
class DisputeCard extends StatelessWidget {
  final Map<String, dynamic> d;
  const DisputeCard(this.d, {super.key});

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;
    final verdict = (d['verdict'] ?? 'unclear').toString();
    final status = (d['status'] ?? '').toString();
    final diffs = ((d['differences'] as List?) ?? []).cast<Map<String, dynamic>>();
    final adjusted = (d['adjusted'] ?? '').toString();
    final conf = d['confidence'] is num ? ((d['confidence'] as num) * 100).round() : null;
    final label = {
          'match': 'Photo looks like the listing',
          'mismatch': 'Photo does not match the listing',
          'unclear': 'Photo check was unclear',
        }[verdict] ??
        verdict;
    final colour = verdict == 'mismatch'
        ? cs.errorContainer.withAlpha(110)
        : (verdict == 'match' ? Colors.green.withAlpha(50) : cs.surfaceContainerHighest);
    return Container(
      margin: const EdgeInsets.only(top: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: colour, borderRadius: BorderRadius.circular(10)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          const Icon(Icons.image_search, size: 18),
          const SizedBox(width: 6),
          Expanded(child: Text(label, style: t.titleSmall)),
          if (conf != null) Text('$conf% sure', style: t.labelSmall),
        ]),
        const SizedBox(height: 6),
        if ((d['promised'] ?? '').toString().isNotEmpty) Text('Listed: ${d['promised']}', style: t.bodySmall),
        if ((d['observed'] ?? '').toString().isNotEmpty) Text('Photo shows: ${d['observed']}', style: t.bodySmall),
        for (final x in diffs)
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(x['severity'] == 'major' ? Icons.error_outline : Icons.info_outline,
                  size: 15, color: x['severity'] == 'major' ? cs.error : null),
              const SizedBox(width: 6),
              Expanded(
                  child: Text('${x['severity'] == 'major' ? 'Major' : 'Minor'}: ${x['text']}', style: t.bodySmall)),
            ]),
          ),
        if (adjusted.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text('Konsyer adjusted the model\'s answer: $adjusted.', style: t.labelSmall),
          ),
        if ((d['note'] ?? '').toString().isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text('Customer note: ${d['note']}', style: t.bodySmall),
          ),
        if ((d['message'] ?? '').toString().isNotEmpty && status.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(d['message'].toString(), style: t.bodySmall),
        ],
        const SizedBox(height: 6),
        Text('A model\'s recommendation only. A person decides; the photo is not stored.', style: t.labelSmall),
      ]),
    );
  }
}
