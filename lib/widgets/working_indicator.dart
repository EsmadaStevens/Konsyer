import 'dart:async';
import 'package:flutter/material.dart';
import 'package:konsyer/core/theme.dart';

/// Shown while the agent works, so the screen never looks frozen. It says only what is true: it is working, and a web
/// search can be slow. It does not pretend to know which step the agent is on.
class WorkingIndicator extends StatefulWidget {
  const WorkingIndicator();
  @override
  State<WorkingIndicator> createState() => _WorkingIndicatorState();
}

class _WorkingIndicatorState extends State<WorkingIndicator> {
  Timer? _t;
  int _n = 0;

  @override
  void initState() {
    super.initState();
    _t = Timer.periodic(const Duration(milliseconds: 500), (_) {
      if (mounted) setState(() => _n++);
    });
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dots = '.' * (_n % 4);
    final slow = _n >= 16; // after about 8 seconds
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: K.surface,
          border: Border.all(color: K.border),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: K.gold)),
          const SizedBox(width: 12),
          Flexible(
            child: Text(
              slow ? 'Still working. A web search can take up to 30 seconds$dots' : 'Konsyer is working on it$dots',
              style: const TextStyle(color: K.muted),
            ),
          ),
        ]),
      ),
    );
  }
}
