import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import 'api.dart';

/// DEMO admin console, for the video. The admin key is typed here each time and is never saved.
/// A production version would live behind real admin sign-in, not in the customer app.
class AdminScreen extends StatefulWidget {
  const AdminScreen({super.key});
  @override
  State<AdminScreen> createState() => _AdminScreenState();
}

class _AdminScreenState extends State<AdminScreen> {
  final _key = TextEditingController();
  List<Map<String, dynamic>> _rows = [];
  bool _busy = false;
  String? _error;

  Map<String, String> get _headers => {'X-Admin-Key': _key.text.trim()};

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  Future<void> _load() async {
    setState(() => _busy = true);
    try {
      final j = await apiGet('/admin/merchants', headers: _headers);
      if (mounted) {
        setState(() {
          _rows = (j['merchants'] as List).cast<Map<String, dynamic>>();
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _rows = [];
          _error = '$e';
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _verify(int id) async {
    setState(() => _busy = true);
    try {
      final j = await apiPost('/admin/merchants/$id/verify', {}, headers: _headers);
      _toast('Verified. ${j['listings_verified']} listing(s) are now live.');
      await _load();
    } catch (e) {
      _toast('$e');
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _open(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null || uri.scheme != 'https') return _toast('Only https links can be opened.');
    try {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (_) {
      _toast('Could not open the link.');
    }
  }

  Widget _card(Map<String, dynamic> m) {
    final t = Theme.of(context).textTheme;
    final verified = m['verified'] == true;
    final review = m['needs_review'] == true;
    final social = (m['social_url'] ?? '').toString();
    final phone = (m['whatsapp'] ?? '').toString();
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text(m['name'].toString(), style: t.titleMedium)),
            Chip(
              label: Text(verified ? 'Verified' : 'Not verified'),
              visualDensity: VisualDensity.compact,
            ),
          ]),
          Text([
            if ((m['area'] ?? '').toString().isNotEmpty) m['area'].toString(),
            if (phone.isNotEmpty) '+$phone',
          ].join('  -  ')),
          if (social.isNotEmpty)
            Row(children: [
              Expanded(child: SelectableText(social, style: t.bodySmall)),
              IconButton(
                  tooltip: 'Open the page to check the name and number',
                  icon: const Icon(Icons.open_in_new),
                  onPressed: () => _open(social)),
            ])
          else
            Text('No page link given.', style: t.bodySmall),
          Text('Listings awaiting verification: ${m['claimed_listings']}   Live: ${m['verified_listings']}',
              style: t.bodySmall),
          const SizedBox(height: 8),
          FilledButton(
            onPressed: (_busy || !review) ? null : () => _verify(m['merchant_id'] as int),
            child: Text(review ? 'Verify this business' : 'Nothing to review'),
          ),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Demo admin console')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
              color: cs.errorContainer.withAlpha(90), borderRadius: BorderRadius.circular(10)),
          child: const Text('Demo console for the video. Verification is a human decision: check the business '
              'page against the name and number before you verify. The key is typed here and never saved.'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _key,
          obscureText: true,
          onSubmitted: (_) => _load(),
          decoration: const InputDecoration(labelText: 'Admin key', border: OutlineInputBorder()),
        ),
        const SizedBox(height: 8),
        FilledButton(onPressed: _busy ? null : _load, child: Text(_busy ? 'Working...' : 'Load businesses')),
        if (_error != null)
          Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: cs.error))),
        const SizedBox(height: 8),
        for (final m in _rows) _card(m),
      ]),
    );
  }
}