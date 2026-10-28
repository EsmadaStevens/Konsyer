import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

// Set with: --dart-define=API_BASE=http://localhost:8000
// (Android emulator: http://10.0.2.2:8000)
const apiBase = String.fromEnvironment('API_BASE', defaultValue: 'http://localhost:8000');

String fmtK(int ngwee) {
  final whole = ngwee ~/ 100;
  final frac = ngwee % 100;
  final s = whole.toString().replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (m) => ',');
  return frac == 0 ? 'K$s' : 'K$s.${frac.toString().padLeft(2, '0')}';
}

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
      );
}

class Msg {
  final bool fromUser;
  final String text;
  final List<Map<String, dynamic>> cards;
  Msg(this.fromUser, this.text, [this.cards = const []]);
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
    Msg(false, 'Hi! Tell me what you want to do, your budget and group size, and I will plan it.'),
  ];
  bool _loading = false;

  Future<void> _send() async {
    final text = _ctrl.text.trim();
    if (text.isEmpty || _loading) return;
    setState(() {
      _msgs.add(Msg(true, text));
      _loading = true;
      _ctrl.clear();
    });
    try {
      final r = await http
          .post(Uri.parse('$apiBase/chat'),
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode({'user_id': 1, 'message': text}))
          .timeout(const Duration(seconds: 60));
      if (r.statusCode != 200) throw Exception('Server returned ${r.statusCode}');
      final j = jsonDecode(r.body) as Map<String, dynamic>;
      final cards = (j['cards'] as List? ?? []).cast<Map<String, dynamic>>();
      setState(() => _msgs.add(Msg(false, j['reply'] as String? ?? '', cards)));
    } catch (e) {
      setState(() => _msgs.add(Msg(false, 'Could not reach the server: $e')));
    } finally {
      setState(() => _loading = false);
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
      appBar: AppBar(title: const Text('Konsyer')),
      body: Column(children: [
        Expanded(
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.all(12),
            itemCount: _msgs.length,
            itemBuilder: (_, i) => _Bubble(_msgs[i]),
          ),
        ),
        if (_loading) const LinearProgressIndicator(minHeight: 2),
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
              if (c['type'] == 'plan') PlanCard(c),
          ],
        ),
      ),
    );
  }
}

class PlanCard extends StatelessWidget {
  final Map<String, dynamic> c;
  const PlanCard(this.c, {super.key});
  @override
  Widget build(BuildContext context) {
    final items = (c['items'] as List).cast<Map<String, dynamic>>();
    final total = c['total'] as int;
    final budget = c['budget'] as int;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c['title'] as String, style: Theme.of(context).textTheme.titleMedium),
          if (c['sample_data'] == true)
            Text('Sample data', style: Theme.of(context).textTheme.labelSmall),
          const Divider(),
          for (final i in items)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(children: [
                SizedBox(width: 48, child: Text(i['time'] as String)),
                Expanded(child: Text(i['label'] as String)),
                Text(fmtK(i['amount'] as int)),
              ]),
            ),
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