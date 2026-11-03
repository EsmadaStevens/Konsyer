import 'package:flutter/material.dart';
import 'package:konsyer/core/api.dart';
import 'package:konsyer/core/theme.dart';
import 'package:konsyer/widgets/pill.dart';

class PlanCard extends StatefulWidget {
  final Map<String, dynamic> c;
  const PlanCard(this.c, {super.key});
  @override
  State<PlanCard> createState() => _PlanCardState();
}

class _PlanCardState extends State<PlanCard> {
  late Map<String, dynamic> c;
  int? busyId;

  @override
  void initState() {
    super.initState();
    c = widget.c;
  }

  void _toast(String msg) {
    if (!mounted || msg.isEmpty) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _act(int id, String verb) async {
    setState(() => busyId = id);
    try {
      final j = await apiPost('/actions/$id/$verb', {'user_id': userId});
      if (mounted && j['card'] is Map) {
        setState(() => c = (j['card'] as Map).cast<String, dynamic>());
      }
      _toast(j['message']?.toString() ?? '');
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busyId = null);
    }
  }

  Widget _item(Map<String, dynamic> i) {
    final cs = Theme.of(context).colorScheme;
    final small = Theme.of(context).textTheme.bodySmall;
    final id = i['action_id'] as int?;
    final status = (i['status'] ?? '').toString();
    final decision = i['decision']?.toString();
    final reason = i['reason']?.toString();
    final busy = id != null && busyId == id;
    final open = id != null && (status == 'pending' || status == 'approved');

    Widget? footer;
    if (open) {
      footer = Wrap(spacing: 8, children: [
        FilledButton(
          onPressed: busy ? null : () => _act(id, 'approve'),
          child: Text(busy ? 'Working...' : 'Approve'),
        ),
        OutlinedButton(
          onPressed: busy ? null : () => _act(id, 'deny'),
          child: const Text('Decline'),
        ),
      ]);
    } else if (status == 'executed') {
      footer = Text('Booked. Money is held until you check in (sandbox wallet).',
          style: small?.copyWith(color: K.ok));
    } else if (status == 'denied') {
      footer = Text(decision == 'DENY' ? 'Blocked by your limits. Nothing charged.' : 'Declined. Nothing charged.',
          style: small?.copyWith(color: cs.error));
    } else if (status == 'failed') {
      footer = Text('Failed. Nothing charged.', style: small?.copyWith(color: cs.error));
    }

    final showReason = reason != null && reason.isNotEmpty && (open || status == 'denied');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          SizedBox(width: 48, child: Text((i['time'] ?? '').toString())),
          if (i['photo_url'] != null)
            Padding(
              padding: const EdgeInsets.only(right: 10),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: Image.network('$apiBase${i['photo_url']}',
                    width: 56, height: 56, fit: BoxFit.cover,
                    errorBuilder: (c, e, s) => const SizedBox(width: 56, height: 56)),
              ),
            ),
          Expanded(child: Text((i['label'] ?? '').toString())),
          Text(fmtK((i['amount'] as num).toInt())),
        ]),
        if (showReason)
          Padding(
            padding: const EdgeInsets.only(left: 48, top: 2),
            child: Text(reason, style: small?.copyWith(color: cs.onSurfaceVariant)),
          ),
        if (footer != null)
          Padding(padding: const EdgeInsets.only(left: 48, top: 6), child: footer),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final items = (c['items'] as List).cast<Map<String, dynamic>>();
    final total = (c['total'] as num).toInt();
    final budget = (c['budget'] as num).toInt();
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c['title'] as String, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Wrap(spacing: 8, runSpacing: 6, children: [
            if (c['sample_data'] == true) const Pill('Sample data', color: K.warn, icon: Icons.science_outlined),
            const Pill('Sandbox: no real money moves', color: K.muted, icon: Icons.shield_outlined),
          ]),
          const Divider(),
          for (final i in items) _item(i),
          const Divider(),
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            const Text('Total'),
            Text('${fmtK(total)} of ${fmtK(budget)}',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ]),
        ]),
      ),
    );
  }
}
