import 'dart:async';
import 'package:flutter/widgets.dart';

/// Keeps a screen up to date without a manual refresh: it calls [refresh] every few seconds while the screen is open.
/// The screen's own loader must be quiet (no spinner), so the page does not flicker.
mixin AutoRefresh<T extends StatefulWidget> on State<T> {
  Timer? _autoTimer;

  /// Reload the screen's data. Must not throw and must not show a spinner.
  Future<void> refresh();

  void startAutoRefresh([Duration every = const Duration(seconds: 5)]) {
    _autoTimer?.cancel();
    _autoTimer = Timer.periodic(every, (_) {
      if (mounted) refresh();
    });
  }

  @override
  void dispose() {
    _autoTimer?.cancel();
    super.dispose();
  }
}
