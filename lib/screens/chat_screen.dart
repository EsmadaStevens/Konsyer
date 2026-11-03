import 'dart:async';
import 'package:flutter/material.dart';
import 'package:konsyer/core/api.dart';
import 'package:konsyer/core/theme.dart';
import 'package:konsyer/models/chat_message.dart';
import 'package:konsyer/screens/activity_screen.dart';
import 'package:konsyer/screens/merchant_screen.dart';
import 'package:konsyer/screens/vouchers_screen.dart';
import 'package:konsyer/screens/wallet_screen.dart';
import 'package:konsyer/widgets/chat_bubble.dart';
import 'package:konsyer/widgets/nav_actions.dart';
import 'package:konsyer/widgets/working_indicator.dart';

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
    _loadMode();
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

  String _mode = 'live';

  /// live, replay (recorded model answers, no key needed) or stub: say which, so nobody mistakes a recording for a live call.
  Future<void> _loadMode() async {
    try {
      final j = await apiGet('/mode');
      replayMode = j['mode'] == 'replay';
      if (mounted) setState(() => _mode = (j['mode'] ?? 'live').toString());
    } catch (_) {}
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
    _scrollToEnd();
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

  /// What an empty chat shows: who Konsyer is, and tap-to-send examples, so nobody faces a blank screen.
  Widget _welcome() {
    const examples = [
      (Icons.celebration_outlined, 'Saturday, 5 of us, K2,000, something active'),
      (Icons.spa_outlined, 'I want French tip nails. My budget is K300'),
      (Icons.event_available_outlined, 'Book the press-on nails for Saturday for one person. Budget K200'),
      (Icons.restaurant_outlined, 'Plan a dinner for 4 people, my budget is K50'),
      (Icons.content_cut, 'Find me a hair salon in Lusaka'),
    ];
    final t = Theme.of(context).textTheme;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.shield_moon_outlined, size: 40, color: K.gold),
          const SizedBox(height: 12),
          Text('Plan it. Book it. Money held until you are happy.', style: t.titleLarge, textAlign: TextAlign.center),
          const SizedBox(height: 8),
          Text(_greeting, style: t.bodyMedium?.copyWith(color: K.muted), textAlign: TextAlign.center),
          const SizedBox(height: 20),
          Wrap(spacing: 8, runSpacing: 8, alignment: WrapAlignment.center, children: [
            for (final e in examples)
              ActionChip(avatar: Icon(e.$1, size: 18, color: K.gold), label: Text(e.$2), onPressed: () => _send(e.$2)),
          ]),
          const SizedBox(height: 12),
          Text('Tap one, or type your own.', style: t.labelSmall?.copyWith(color: K.muted)),
        ]),
      ),
    );
  }

  void _scrollToEnd() async {
    await Future.delayed(const Duration(milliseconds: 60));
    if (_scroll.hasClients) {
      _scroll.animateTo(_scroll.position.maxScrollExtent + 200,
          duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Konsyer'),
        actions: [
          IconButton(tooltip: 'New chat', icon: const Icon(Icons.add_comment_outlined), onPressed: _newChat),
          ...navActions(context, NavTarget.chat, walletBadge: _attnCount, onReturn: _loadAttention),
        ],
      ),
      body: Column(children: [
        if (_mode == 'replay')
          Container(
            margin: const EdgeInsets.fromLTRB(12, 4, 12, 0),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
                color: K.warn.withAlpha(30), borderRadius: BorderRadius.circular(10), border: Border.all(color: K.warn.withAlpha(110))),
            child: Row(children: [
              const Icon(Icons.replay, size: 16, color: K.warn),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                    'Replay mode: answers are recordings of the real models, so only the recorded examples work. '
                    'Add a model key to go live.',
                    style: Theme.of(context).textTheme.labelSmall),
              ),
            ]),
          ),
        Expanded(
          child: _msgs.length == 1 && !_loading
              ? _welcome()
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.all(12),
                  itemCount: _msgs.length + (_loading ? 1 : 0),
                  itemBuilder: (_, i) => i < _msgs.length ? ChatBubble(_msgs[i]) : const WorkingIndicator(),
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
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _ctrl,
                  onSubmitted: (_) => _send(),
                  decoration: const InputDecoration(hintText: 'e.g. Saturday, 5 of us, K2,000, something active'),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(onPressed: _send, icon: const Icon(Icons.arrow_upward)),
            ]),
          ),
        ),
      ]),
    );
  }
}
