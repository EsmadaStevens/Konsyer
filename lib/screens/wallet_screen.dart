import 'package:flutter/material.dart';
import 'package:konsyer/core/auto_refresh.dart';
import 'package:konsyer/core/api.dart';
import 'package:konsyer/widgets/nav_actions.dart';
import 'package:konsyer/screens/vouchers_screen.dart';

class WalletScreen extends StatefulWidget {
  const WalletScreen({super.key});
  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> with AutoRefresh<WalletScreen> {
  Map<String, dynamic>? w;
  String? error;
  bool busy = false;

  @override
  void initState() {
    super.initState();
    _load();
    startAutoRefresh();
  }

  @override
  Future<void> refresh() => _load();

  Future<void> _load() async {
    try {
      final j = await apiGet('/wallet?user_id=$userId');
      if (mounted) setState(() { w = j; error = null; });
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> _topup(int ngwee) async {
    setState(() => busy = true);
    try {
      await apiPost('/wallet/topup', {
        'user_id': userId,
        'amount_ngwee': ngwee,
        'idempotency_key': 'ui-${DateTime.now().microsecondsSinceEpoch}',
      });
      await _load();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final j = w;
    return Scaffold(
      appBar: AppBar(title: Text('Wallet'), actions: navActions(context, NavTarget.wallet)),
      body: error != null
          ? Center(child: Text('Could not load wallet: $error'))
          : j == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(padding: const EdgeInsets.all(16), children: [
                    Text('Sandbox wallet: test money only', style: t.labelMedium),
                    const SizedBox(height: 8),
                    Text(fmtK((j['balance'] as num).toInt()), style: t.displaySmall),
                    Text('Spent today: ${fmtK((j['spent_today'] as num).toInt())}'),
                    Text('Held for sellers: ${fmtK(((j['held'] as num?) ?? 0).toInt())}'),
                    const SizedBox(height: 8),
                    OutlinedButton.icon(
                      onPressed: () async {
                        await Navigator.of(context)
                            .push(MaterialPageRoute(builder: (_) => const VouchersScreen()));
                        _load();
                      },
                      icon: const Icon(Icons.lock_outline),
                      label: const Text('Held payments'),
                    ),
                    const SizedBox(height: 12),
                    Text('Your limits', style: t.titleSmall),
                    Text('Per purchase: ${fmtK((j['per_purchase_cap'] as num).toInt())}'),
                    Text('Per day: ${fmtK((j['daily_cap'] as num).toInt())}'),
                    Text('Approval mode: ${j['autonomy']}'),
                    const SizedBox(height: 16),
                    FilledButton.icon(
                      onPressed: busy ? null : () => _topup(50000),
                      icon: const Icon(Icons.add),
                      label: const Text('Add K500 (sandbox)'),
                    ),
                    const SizedBox(height: 20),
                    Text('Recent activity', style: t.titleSmall),
                    const Divider(),
                    for (final x in (j['recent'] as List).cast<Map<String, dynamic>>())
                      ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        title: Text((x['ref'] ?? x['kind']).toString()),
                        subtitle: Text((x['created_at'] ?? '').toString()),
                        trailing: Text(fmtK((x['amount'] as num).toInt())),
                      ),
                  ]),
                ),
    );
  }
}
