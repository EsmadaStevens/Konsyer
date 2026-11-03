import 'package:flutter/material.dart';
import 'package:konsyer/core/api.dart';
import 'package:konsyer/widgets/nav_actions.dart';

/// "Activity": what each Meta model did, and who acted on each step (model, rule, person).
/// Model calls are metadata only (job, model, time). Prompts, photos and answers are never stored.
class ActivityScreen extends StatefulWidget {
  const ActivityScreen({super.key});
  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen> {
  Map<String, dynamic>? _data;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final j = await apiGet('/activity?user_id=$userId&limit=40');
      if (mounted) setState(() {
            _data = j;
            _error = null;
          });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  static const _actors = {
    'model': ['Model proposed', Icons.auto_awesome],
    'rule': ['Rule allowed', Icons.rule],
    'rule_blocked': ['Rule blocked', Icons.block],
    'person': ['Person decided', Icons.person_outline],
  };

  String _time(dynamic raw) {
    final s = (raw ?? '').toString();
    final t = DateTime.tryParse(s.contains('T') ? s : s.replaceFirst(' ', 'T') + 'Z');
    if (t == null) return s;
    final l = t.toUtc().add(const Duration(hours: 2)); // Lusaka (CAT)
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(l.day)}/${two(l.month)} ${two(l.hour)}:${two(l.minute)}';
  }

  Widget _totals(List<Map<String, dynamic>> totals) {
    final t = Theme.of(context).textTheme;
    if (totals.isEmpty) {
      return const Padding(padding: EdgeInsets.all(8), child: Text('No model calls yet. Ask the concierge something.'));
    }
    return Column(children: [
      for (final x in totals)
        Card(
          margin: const EdgeInsets.symmetric(vertical: 4),
          child: ListTile(
            title: Text(x['what'].toString(), style: t.titleSmall),
            subtitle: Text('${x['worked']} of ${x['calls']} worked  -  avg ${((x['avg_ms'] as num) / 1000).toStringAsFixed(1)}s'),
          ),
        ),
    ]);
  }

  Widget _audit(Map<String, dynamic> a) {
    final cs = Theme.of(context).colorScheme;
    final actor = _actors[a['actor']] ?? _actors['rule']!;
    final blocked = a['actor'] == 'rule_blocked';
    return ListTile(
      dense: true,
      leading: Icon(actor[1] as IconData, color: blocked ? cs.error : null),
      title: Text('${actor[0]}: ${(a['event'] ?? '').toString().replaceAll('_', ' ')}'),
      subtitle: Text('${(a['why'] ?? '').toString()}\n${_time(a['created_at'])}'),
      isThreeLine: true,
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final d = _data;
    return Scaffold(
      appBar: AppBar(title: Text('Activity'), actions: navActions(context, NavTarget.activity)),
      body: _error != null
          ? Center(child: Padding(padding: const EdgeInsets.all(16), child: Text('Could not load: $_error')))
          : d == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(padding: const EdgeInsets.all(16), children: [
                    Text('The models read and recommend. Rules enforce. People decide the money.', style: t.titleMedium),
                    const SizedBox(height: 4),
                    Text(d['note'].toString(), style: t.labelSmall),
                    const SizedBox(height: 12),
                    Text('What the Meta models did', style: t.titleSmall),
                    _totals((d['model_totals'] as List).cast<Map<String, dynamic>>()),
                    const SizedBox(height: 12),
                    Text('Who acted on each step', style: t.titleSmall),
                    for (final a in (d['audit'] as List).cast<Map<String, dynamic>>()) _audit(a),
                  ]),
                ),
    );
  }
}
