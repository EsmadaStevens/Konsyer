import 'dart:async';
import 'package:flutter/material.dart';
import '/screens/activity_screen.dart';
import '/screens/admin_screen.dart';
import '/api.dart';
import '/contact_card.dart';
import '/screens/merchant_screen.dart';
import '/screens/vouchers_screen.dart';

void main() => runApp(const ConciergeApp());

class ConciergeApp extends StatelessWidget {
  const ConciergeApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Konsyer',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          colorScheme: ColorScheme.fromSeed(
              seedColor: const Color(0xFFD4AF37), brightness: Brightness.dark),
        ),
        home: const ChatScreen(),
        // The admin console is a separate address (open /#/admin in its own tab), not part of the customer app.
        routes: {'/admin': (_) => const AdminScreen()},
      );
}

class Msg {
  final bool fromUser;
  final String text;
  final List<Map<String, dynamic>> cards;
  final bool suggestWeb; // offer a one-tap "Also search the web"
  Msg(this.fromUser, this.text, [this.cards = const [], this.suggestWeb = false]);
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});
  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _ctrl = TextEditingController();
  final _scroll = ScrollController();
  final List<Msg> _msgs = [
    Msg(false, _greeting),
  ];
  bool _loading = false;
  Timer? _timer;
  int _attnCount = 0;
  List<Map<String, dynamic>> _attn = [];

  @override
  void initState() {
    super.initState();
    _loadHistory();
    _loadAttention();
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _loadAttention());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _ctrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  static const _greeting = 'Hi! Tell me what you want to do, your budget and group size, and I will plan it.';

  /// The conversation is kept on the server, so a reload picks up where you left off.
  Future<void> _loadHistory() async {
    try {
      final j = await apiGet('/chat/history?user_id=$userId');
      final rows = ((j['messages'] as List?) ?? []).cast<Map<String, dynamic>>();
      if (!mounted || rows.isEmpty) return;
      setState(() {
        _msgs
          ..clear()
          ..add(Msg(false, _greeting))
          ..addAll([
            for (final r in rows)
              Msg(r['from_user'] == true, (r['text'] ?? '').toString(),
                  ((r['cards'] as List?) ?? []).cast<Map<String, dynamic>>(), r['suggest_web'] == true)
          ]);
      });
      await Future.delayed(const Duration(milliseconds: 100));
      if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    } catch (_) {
      // a fresh conversation is fine if the history cannot be loaded
    }
  }

  Future<void> _newChat() async {
    try {
      await apiPost('/chat/clear', {'user_id': userId});
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      _msgs
        ..clear()
        ..add(Msg(false, _greeting));
    });
  }

  /// Offers, claims waiting for a reply and fresh decisions, so nobody has to dig through screens to find them.
  Future<void> _loadAttention() async {
    try {
      final j = await apiGet('/attention?user_id=$userId');
      if (!mounted) return;
      setState(() {
        _attnCount = (j['count'] as num? ?? 0).toInt();
        _attn = ((j['items'] as List?) ?? []).cast<Map<String, dynamic>>();
      });
    } catch (_) {
      // the banner is a convenience: stay quiet if the server is busy
    }
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));
    _loadAttention();
  }

  Future<void> _send([String? override]) async {
    final text = (override ?? _ctrl.text).trim();
    if (text.isEmpty || _loading) return;
    // The last few messages (text only) so the agent remembers what was said; cards are not sent.
    final said = _msgs.where((m) => m.text.trim().isNotEmpty).toList();
    final recent = said.length > 10 ? said.sublist(said.length - 10) : said;
    final history = [
      for (final m in recent) {'role': m.fromUser ? 'user' : 'assistant', 'content': m.text}
    ];
    setState(() {
      _msgs.add(Msg(true, text));
      _loading = true;
      _ctrl.clear();
    });
    try {
      final j = await apiPost('/chat', {'user_id': userId, 'message': text, 'history': history});
      final cards = (j['cards'] as List? ?? []).cast<Map<String, dynamic>>();
      setState(() => _msgs.add(Msg(false, j['reply'] as String? ?? '', cards, j['suggest_web'] == true)));
    } catch (e) {
      setState(() => _msgs.add(Msg(false, 'Could not reach the server: $e')));
    } finally {
      setState(() => _loading = false);
      _loadAttention();
      await Future.delayed(const Duration(milliseconds: 50));
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent + 200,
            duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Konsyer'),
        actions: [
          IconButton(tooltip: 'New chat', icon: const Icon(Icons.add_comment_outlined), onPressed: _newChat),
          IconButton(
            tooltip: 'For businesses',
            icon: const Icon(Icons.storefront_outlined),
            onPressed: () => _open(const MerchantScreen()),
          ),
          IconButton(
            tooltip: 'Activity: what the models did',
            icon: const Icon(Icons.timeline_outlined),
            onPressed: () => _open(const ActivityScreen()),
          ),
          IconButton(
            tooltip: 'Wallet',
            icon: Badge(
                isLabelVisible: _attnCount > 0,
                label: Text('$_attnCount'),
                child: const Icon(Icons.account_balance_wallet_outlined)),
            onPressed: () => _open(const WalletScreen()),
          ),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.all(12),
            itemCount: _msgs.length,
            itemBuilder: (_, i) => _Bubble(_msgs[i]),
          ),
        ),
        if (_attn.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
            child: Card(
              margin: EdgeInsets.zero,
              color: Theme.of(context).colorScheme.primaryContainer.withAlpha(90),
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: () => _open(_attn.first['target'] == 'business' ? const MerchantScreen() : const VouchersScreen()),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  child: Row(children: [
                    Icon(_attn.first['kind'] == 'action' ? Icons.notifications_active_outlined : Icons.info_outline,
                        size: 20),
                    const SizedBox(width: 10),
                    Expanded(
                        child: Text(
                            _attn.length > 1
                                ? '${_attn.first['text']}  (+${_attn.length - 1} more)'
                                : '${_attn.first['text']}',
                            style: Theme.of(context).textTheme.bodySmall)),
                    const Icon(Icons.chevron_right),
                  ]),
                ),
              ),
            ),
          ),
        if (_loading) const LinearProgressIndicator(minHeight: 2),
        if (!_loading && _msgs.isNotEmpty && _msgs.last.suggestWeb)
          Align(
            alignment: Alignment.centerLeft,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
              child: ActionChip(
                avatar: const Icon(Icons.travel_explore, size: 18),
                label: const Text('Also search the web'),
                onPressed: () => _send('Also search the web for this.'),
              ),
            ),
          ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _ctrl,
                  onSubmitted: (_) => _send(),
                  decoration: const InputDecoration(
                      hintText: 'e.g. Saturday, 5 of us, K2,000, something active',
                      border: OutlineInputBorder()),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(onPressed: _send, icon: const Icon(Icons.send)),
            ]),
          ),
        ),
      ]),
    );
  }
}

class _Bubble extends StatelessWidget {
  final Msg m;
  const _Bubble(this.m);
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Align(
      alignment: m.fromUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.9),
        child: Column(
          crossAxisAlignment: m.fromUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            Container(
              margin: const EdgeInsets.symmetric(vertical: 4),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: m.fromUser ? cs.primaryContainer : cs.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Text(m.text),
            ),
            for (final c in m.cards)
              if (c['type'] == 'plan')
                PlanCard(c)
              else if (c['type'] == 'contact')
                ContactCard(c),
          ],
        ),
      ),
    );
  }
}

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
          style: small?.copyWith(color: Colors.greenAccent));
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
    final small = Theme.of(context).textTheme.labelSmall;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c['title'] as String, style: Theme.of(context).textTheme.titleMedium),
          if (c['sample_data'] == true) Text('Sample data', style: small),
          Text('Sandbox wallet: no real money moves', style: small),
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

class WalletScreen extends StatefulWidget {
  const WalletScreen({super.key});
  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> {
  Map<String, dynamic>? w;
  String? error;
  bool busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

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
      appBar: AppBar(title: const Text('Wallet')),
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