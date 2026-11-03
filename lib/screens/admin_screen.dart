import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:konsyer/core/auto_refresh.dart';
import 'package:konsyer/cards/dispute_card.dart';
import 'package:konsyer/core/api.dart';
import 'package:konsyer/widgets/photo_thumb.dart';

/// Konsyer admin: verify businesses and decide photo reviews. The admin key is typed here each time and never saved.
/// A production version would live behind real admin sign-in, not in the customer app.
class AdminScreen extends StatefulWidget {
  const AdminScreen({super.key});
  @override
  State<AdminScreen> createState() => _AdminScreenState();
}

/// Clock controls exist only in recording builds: flutter run --dart-define=DEMO=true
const _demo = bool.fromEnvironment('DEMO');

class _AdminScreenState extends State<AdminScreen> with AutoRefresh<AdminScreen> {
  final _key = TextEditingController();
  List<Map<String, dynamic>> _rows = [];
  List<Map<String, dynamic>> _disputes = [];
  bool _busy = false;
  String? _error;

  Map<String, String> get _headers => {'X-Admin-Key': _key.text.trim()};

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  @override
  void initState() {
    super.initState();
    startAutoRefresh();
  }

  /// Once the key has been typed and the lists loaded, keep them current. Quiet: no "Working..." flicker, and an error
  /// (the server busy, say) leaves what is on screen alone.
  @override
  Future<void> refresh() async {
    if (_busy) return;
    // The lists are only for someone holding the key: empty the key and they disappear.
    if (_key.text.trim().isEmpty) {
      if (_rows.isNotEmpty || _disputes.isNotEmpty) {
        setState(() {
          _rows = [];
          _disputes = [];
        });
      }
      return;
    }
    if (_rows.isEmpty && _disputes.isEmpty && _error != null) return;
    try {
      final j = await apiGet('/admin/merchants', headers: _headers);
      final dj = await apiGet('/admin/disputes', headers: _headers);
      if (!mounted) return;
      setState(() {
        _disputes = (dj['disputes'] as List).cast<Map<String, dynamic>>();
        _rows = (j['merchants'] as List).cast<Map<String, dynamic>>();
      });
    } catch (e) {
      // A wrong or removed key must not leave the old lists on screen; any other error (server busy) keeps them.
      if (mounted && e.toString().toLowerCase().contains('admin')) {
        setState(() {
          _rows = [];
          _disputes = [];
          _error = '$e';
        });
      }
    }
  }

  Future<void> _load() async {
    setState(() => _busy = true);
    try {
      final j = await apiGet('/admin/merchants', headers: _headers);
      final dj = await apiGet('/admin/disputes', headers: _headers);
      if (mounted) {
        setState(() {
          _disputes = (dj['disputes'] as List).cast<Map<String, dynamic>>();
          _rows = (j['merchants'] as List).cast<Map<String, dynamic>>();
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _rows = [];
          _disputes = [];
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

  Future<void> _resolve(Map<String, dynamic> d, String decision) async {
    setState(() => _busy = true);
    try {
      await apiPost('/admin/disputes/${d['id']}/resolve', {'decision': decision}, headers: _headers);
      _toast(decision == 'refund' ? 'Refunded the customer.' : 'Claim rejected. The payment continues.');
      await _load();
    } catch (e) {
      _toast('$e');
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _disputeCard(Map<String, dynamic> d) {
    final t = Theme.of(context).textTheme;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text((d['label'] ?? '').toString(), style: t.titleMedium)),
            Text(fmtK((d['amount'] as num).toInt())),
          ]),
          Text('Seller: ${d['payee_name']}${d['payee_verified'] == true ? ' (verified)' : ''}  -  ${d['stage'] == 'konsyer' ? 'needs Konsyer to decide' : 'waiting for the seller to reply'}', style: t.bodySmall),
          DisputeCard(d, audience: DisputeAudience.admin, loadPhoto: () => apiGetBytes('/admin/disputes/${d['id']}/photo', headers: _headers)),
          const SizedBox(height: 8),
          Wrap(spacing: 8, runSpacing: 8, children: [
            FilledButton(onPressed: _busy ? null : () => _resolve(d, 'refund'), child: const Text('Refund customer')),
            OutlinedButton(onPressed: _busy ? null : () => _resolve(d, 'reject'), child: const Text('Reject claim')),
          ]),
        ]),
      ),
    );
  }

  Future<void> _advance() async {
    setState(() => _busy = true);
    try {
      final j = await apiPost('/admin/advance-time', {'hours': 24}, headers: _headers);
      _toast('Clock moved forward 24 hours. App time is now ${j['demo_now']}.');
    } catch (e) {
      _toast('$e');
    } finally {
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
          for (final l in ((m['pending_listings'] as List?) ?? []).cast<Map<String, dynamic>>())
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest.withAlpha(120),
                    borderRadius: BorderRadius.circular(8)),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  if (l['has_photo'] == true) ...[
                    PhotoThumb(
                        size: 72,
                        load: () => apiGetBytes('/listings/${l['listing_id']}/photo', headers: _headers)),
                    const SizedBox(width: 10),
                  ],
                  Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${l['title']}  -  ${fmtK((l['price'] as num).toInt())}', style: t.titleSmall),
                  Text((l['description'] ?? '').toString().isEmpty ? 'No description.' : l['description'].toString(),
                      style: t.bodySmall),
                  Text(l['photo_checked'] == true
                          ? 'Photo check: on (the description is specific).'
                          : 'Photo check: off (too vague, so disputes go straight to a person).',
                      style: t.labelSmall),
                  ])),
                ]),
              ),
            ),
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
      appBar: AppBar(title: const Text('Konsyer admin')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
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
        if (_demo) ...[
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _busy ? null : _advance,
            icon: const Icon(Icons.fast_forward_outlined),
            label: const Text('Move the clock forward 24 hours'),
          ),
        ],
        const SizedBox(height: 8),
        if (_disputes.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('Photo reviews waiting for a person', style: Theme.of(context).textTheme.titleMedium),
          for (final d in _disputes) _disputeCard(d),
          const Divider(height: 24),
        ],
        for (final m in _rows) _card(m),
      ]),
    );
  }
}