import 'package:flutter/material.dart';
import 'package:konsyer/cards/contact_card.dart';
import 'package:konsyer/core/api.dart';

/// Web leads, drawn from the data by the app (not from the model's wording), so a price hint
/// always shows exactly as the page worded it. Every lead is UNVERIFIED. Nothing is sent:
/// the Message button only drafts a message; the user taps send in their own app.
class LeadsCard extends StatefulWidget {
  final Map<String, dynamic> c;
  const LeadsCard(this.c, {super.key});
  @override
  State<LeadsCard> createState() => _LeadsCardState();
}

class _LeadsCardState extends State<LeadsCard> {
  late List<Map<String, dynamic>> _leads;
  final Map<int, Map<String, dynamic>> _drafts = {};
  final Set<int> _busy = {};
  final Set<int> _hidden = {};

  static const _options = <Map<String, String?>>[
    {'status': 'open', 'price': null, 'label': 'They replied and it is open'},
    {'status': 'no_reply', 'price': null, 'label': 'No reply yet'},
    {'status': 'closed', 'price': null, 'label': 'Closed, wrong number or no longer offered'},
    {'status': null, 'price': 'matched', 'label': 'The price matched what the page said'},
    {'status': null, 'price': 'differs', 'label': 'The price was different'},
  ];

  @override
  void initState() {
    super.initState();
    _leads = (widget.c['leads'] as List).map((e) => Map<String, dynamic>.from(e as Map)).toList();
  }

  void _toast(String m) {
    if (mounted && m.isNotEmpty) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  int _id(Map<String, dynamic> lead) => (lead['lead_id'] as num).toInt();

  Future<void> _draft(Map<String, dynamic> lead) async {
    final id = _id(lead);
    final ctrl = TextEditingController();
    final details = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Message ${lead['name']}'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLines: 2,
          decoration: const InputDecoration(
            labelText: 'What do you need?',
            hintText: 'e.g. braids Saturday afternoon, budget K300',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, ctrl.text.trim()), child: const Text('Draft message')),
        ],
      ),
    );
    if (details == null || details.isEmpty) return;
    setState(() => _busy.add(id));
    try {
      final j = await apiPost('/leads/$id/contact', {'user_id': userId, 'details': details});
      if (mounted) setState(() => _drafts[id] = j);
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy.remove(id));
    }
  }

  Future<void> _report(Map<String, dynamic> lead) async {
    final id = _id(lead);
    final pick = await showModalBottomSheet<Map<String, String?>>(
      context: context,
      builder: (c) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(padding: EdgeInsets.all(16), child: Text('What happened when you contacted them?')),
          for (final o in _options) ListTile(title: Text(o['label']!), onTap: () => Navigator.pop(c, o)),
        ]),
      ),
    );
    if (pick == null) return;
    setState(() => _busy.add(id));
    try {
      final j = await apiPost('/leads/$id/feedback', {
        'user_id': userId,
        if (pick['status'] != null) 'status': pick['status'],
        if (pick['price'] != null) 'price': pick['price'],
      });
      if (!mounted) return;
      setState(() {
        lead['signals'] = j['signals'];
        lead['mine'] = j['mine'];
        if (j['hidden'] == true) _hidden.add(id);
      });
      _toast(j['hidden'] == true
          ? 'Thanks. This lead is hidden from your future searches.'
          : 'Thanks. This helps other people spot stale or wrong listings.');
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy.remove(id));
    }
  }

  String _signals(Map<String, dynamic> lead) {
    final s = (lead['signals'] as Map?) ?? {};
    int n(String k) => ((s[k] as num?) ?? 0).toInt();
    final parts = <String>[
      if (n('open') > 0) '${n('open')} said it is open',
      if (n('no_reply') > 0) '${n('no_reply')} got no reply',
      if (n('closed') > 0) '${n('closed')} said it is closed',
      if (n('price_matched') > 0) '${n('price_matched')} said the price matched',
      if (n('price_differs') > 0) '${n('price_differs')} said the price was different',
    ];
    return parts.isEmpty ? 'No reports from other users yet.' : 'Other users: ${parts.join(', ')}.';
  }

  Widget _lead(Map<String, dynamic> lead) {
    final t = Theme.of(context).textTheme;
    final id = _id(lead);
    final price = (lead['price_text'] ?? '').toString();
    final priceFor = (lead['price_for'] ?? '').toString();
    final area = (lead['area'] ?? '').toString();
    final draft = _drafts[id];
    final busy = _busy.contains(id);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(lead['name'].toString(), style: t.titleSmall),
        if (area.isNotEmpty) Text(area, style: t.bodySmall),
        const SizedBox(height: 2),
        Text(
          price.isEmpty
              ? 'Price: not listed, ask the seller'
              : 'Price on the page: $price${priceFor.isEmpty ? '' : ' ($priceFor)'}. A hint: confirm with the seller.',
        ),
        if (lead['corroborated'] == true)
          Text('The same number was found on more than one site.', style: t.labelSmall),
        if (lead['sample_data'] == true) Text('Sample data', style: t.labelSmall),
        Text(_signals(lead), style: t.labelSmall),
        const SizedBox(height: 6),
        Wrap(spacing: 8, runSpacing: 4, children: [
          FilledButton.tonalIcon(
            onPressed: busy ? null : () => _draft(lead),
            icon: const Icon(Icons.edit_outlined),
            label: Text(draft == null ? 'Message' : 'New message'),
          ),
          TextButton(onPressed: busy ? null : () => _report(lead), child: const Text('Did they reply?')),
        ]),
        if (draft != null) ContactCard(draft),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final shown = _leads.where((l) => !_hidden.contains(_id(l))).toList();
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text((widget.c['title'] ?? 'Web leads (unverified)').toString(), style: t.titleMedium),
          Text('From web directories and social pages, which can be out of date. Check before you go.',
              style: t.labelSmall),
          const Divider(),
          if (shown.isEmpty) const Text('All of these were hidden by your reports.'),
          for (final l in shown) _lead(l),
          const Divider(),
          Text('Konsyer does not handle payment for sellers outside the app. You and the seller arrange it directly.',
              style: t.labelSmall),
        ]),
      ),
    );
  }
}