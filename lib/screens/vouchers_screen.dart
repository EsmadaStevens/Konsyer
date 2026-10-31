import 'package:flutter/material.dart';
import '../api.dart';

/// "Held payments": money Konsyer holds between you and a seller.
/// Sandbox money and simulated payouts: nothing real moves. Money reaches an unverified seller only when
/// you confirm a mobile-money number on screen; the server checks that number, not this screen.
class VouchersScreen extends StatefulWidget {
  const VouchersScreen({super.key});
  @override
  State<VouchersScreen> createState() => _VouchersScreenState();
}

class _VouchersScreenState extends State<VouchersScreen> {
  List<Map<String, dynamic>>? _rows;
  String? _error;
  bool _busy = false;

  static const _reasons = {
    'seller_cancelled': 'The seller cancelled',
    'not_delivered': 'The service was not delivered',
    'looks_like_scam': 'It looks like a scam',
    'closed_or_unreachable': 'The business is closed or unreachable',
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _toast(String m) {
    if (mounted && m.isNotEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
    }
  }

  Future<void> _load() async {
    try {
      final j = await apiGet('/vouchers?user_id=$userId');
      if (mounted) {
        setState(() {
          _rows = (j['vouchers'] as List).cast<Map<String, dynamic>>();
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<bool> _confirm(String title, String body, String yes) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Not now')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: Text(yes)),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> _run(Future<Map<String, dynamic>> Function() call, String Function(Map<String, dynamic>) done) async {
    setState(() => _busy = true);
    try {
      final j = await call();
      _toast(done(j));
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _release(Map<String, dynamic> v) async {
    if (v['needs_payout_number'] == true) return _releaseUnverified(v);
    final ok = await _confirm(
      'Release ${v['amount_display']}?',
      'Only do this if you received the service from ${v['payee_name']}. '
          'The seller is then paid (simulated).',
      'Yes, release',
    );
    if (!ok) return;
    await _run(() => apiPost('/vouchers/${v['id']}/release', {'user_id': userId}),
        (_) => 'Released. The seller is paid (simulated).');
  }

  Future<void> _releaseUnverified(Map<String, dynamic> v) async {
    final ctrl = TextEditingController(text: (v['payee_phone'] ?? '').toString());
    final typed = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Seller mobile-money number'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('This seller is not verified. Enter the number they told you to pay, '
              'exactly as they gave it to you.'),
          const SizedBox(height: 12),
          TextField(
            controller: ctrl,
            keyboardType: TextInputType.phone,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Mobile number', border: OutlineInputBorder()),
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, ctrl.text.trim()), child: const Text('Check number')),
        ],
      ),
    );
    if (typed == null || typed.isEmpty) return;

    setState(() => _busy = true);
    Map<String, dynamic>? checked;
    try {
      checked = await apiPost('/vouchers/${v['id']}/payout-check', {'user_id': userId, 'payout_number': typed});
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    final info = checked;
    if (info == null || !mounted) return;

    final warning = (info['warning'] ?? '').toString();
    bool accepted = false;
    final go = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, setD) => AlertDialog(
          title: const Text('Confirm before paying'),
          content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Pay ${v['amount_display']} to'),
            Text(info['display'].toString(), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
            Text(info['network_note'].toString(), style: Theme.of(c).textTheme.bodySmall),
            if (warning.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(warning, style: TextStyle(color: Theme.of(c).colorScheme.error)),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                value: accepted,
                onChanged: (x) => setD(() => accepted = x ?? false),
                title: const Text('The seller told me to use this number'),
              ),
            ],
            const SizedBox(height: 8),
            const Text('Once released, this cannot be refunded.'),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Back')),
            FilledButton(
              onPressed: (warning.isNotEmpty && !accepted) ? null : () => Navigator.pop(c, true),
              child: const Text('Confirm and release'),
            ),
          ],
        ),
      ),
    );
    if (go != true) return;
    await _run(
      () => apiPost('/vouchers/${v['id']}/release', {
        'user_id': userId,
        'payout_number': typed,
        'confirmed_number': info['number'],
        'accept_warning': warning.isNotEmpty,
      }),
      (_) => 'Released (simulated payout).',
    );
  }

  Future<void> _cancel(Map<String, dynamic> v) async {
    final ok = await _confirm('Cancel this?', (v['policy_text'] ?? '').toString(), 'Yes, cancel');
    if (!ok) return;
    await _run(() => apiPost('/vouchers/${v['id']}/cancel', {'user_id': userId}), (j) {
      final r = (j['voucher'] as Map).cast<String, dynamic>();
      final back = fmtK((r['refunded_amount'] as num).toInt());
      final kept = (r['paid_amount'] as num).toInt();
      return kept > 0 ? 'Cancelled. $back returned; the seller keeps ${fmtK(kept)}.' : 'Cancelled. $back returned to your wallet.';
    });
  }

  Future<void> _report(Map<String, dynamic> v) async {
    final reason = await showModalBottomSheet<String>(
      context: context,
      builder: (c) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(padding: EdgeInsets.all(16), child: Text('What went wrong?')),
          for (final e in _reasons.entries) ListTile(title: Text(e.value), onTap: () => Navigator.pop(c, e.key)),
        ]),
      ),
    );
    if (reason == null) return;
    await _run(() => apiPost('/vouchers/${v['id']}/report', {'user_id': userId, 'reason': reason}),
        (_) => 'Reported. Your money was returned to your wallet.');
  }

  String _stateLabel(String s) {
    switch (s) {
      case 'held':
        return 'Held';
      case 'released':
        return 'Paid to seller';
      case 'refunded':
        return 'Refunded';
      case 'forfeited':
        return 'Part refunded';
      default:
        return s;
    }
  }

  Widget _card(Map<String, dynamic> v) {
    final t = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;
    final held = v['state'] == 'held';
    final verified = v['payee_verified'] == true;
    final canReport = v['can_report'] == true;
    final paid = (v['paid_amount'] as num).toInt();
    final back = (v['refunded_amount'] as num).toInt();
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text((v['label'] ?? '').toString(), style: t.titleMedium)),
            const SizedBox(width: 8),
            Text(v['amount_display'].toString(), style: t.titleMedium),
          ]),
          const SizedBox(height: 4),
          Wrap(spacing: 8, children: [
            Chip(label: Text(_stateLabel(v['state'].toString())), visualDensity: VisualDensity.compact),
            Chip(
              label: Text(verified ? 'Verified seller' : 'Seller not verified'),
              visualDensity: VisualDensity.compact,
              backgroundColor: verified ? Colors.green.withAlpha(60) : cs.errorContainer.withAlpha(90),
            ),
          ]),
          if ((v['starts'] ?? '').toString().isNotEmpty) Text('When: ${v['starts']}', style: t.bodySmall),
          if (held && (v['policy_text'] ?? '').toString().isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(v['policy_text'].toString(), style: t.bodySmall),
          ],
          if (!held && v['state'] == 'forfeited')
            Text('The seller kept ${fmtK(paid)}; ${fmtK(back)} came back to your wallet.', style: t.bodySmall),
          if (!held && v['state'] == 'refunded') Text('${fmtK(back)} came back to your wallet.', style: t.bodySmall),
          if (!held && v['state'] == 'released')
            Text('Paid (simulated payout ${v['payout_ref'] ?? ''}).', style: t.bodySmall),
          if (held) ...[
            const SizedBox(height: 10),
            Wrap(spacing: 8, runSpacing: 8, children: [
              FilledButton(
                  onPressed: _busy ? null : () => _release(v), child: const Text('I received it')),
              OutlinedButton(onPressed: _busy ? null : () => _cancel(v), child: const Text('Cancel')),
              TextButton(
                  onPressed: (_busy || !canReport) ? null : () => _report(v),
                  child: const Text('Report a problem')),
            ]),
            if (!canReport)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('You can report a no-show after the booking time has started.', style: t.labelSmall),
              ),
          ],
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return Scaffold(
      appBar: AppBar(title: const Text('Held payments')),
      body: _error != null
          ? Center(child: Padding(padding: const EdgeInsets.all(16), child: Text('Could not load: $_error')))
          : rows == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(padding: const EdgeInsets.all(16), children: [
                    Text(
                      'Sandbox money, simulated payouts. Konsyer holds the money until you confirm. '
                      'For verified businesses it is also released 24 hours after the service if nobody objects.',
                      style: Theme.of(context).textTheme.labelMedium,
                    ),
                    const SizedBox(height: 8),
                    if (rows.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('Nothing is held right now.')),
                    for (final v in rows) _card(v),
                  ]),
                ),
    );
  }
}