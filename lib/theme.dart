import 'package:flutter/material.dart';

/// One place for how Konsyer looks. The colours are the ones the app already used (warm dark with gold);
/// this file only gives them structure: surfaces, borders, rounded shapes, and toasts that match the app.
class K {
  static const bg = Color(0xFF15120B);
  static const surface = Color(0xFF1F1B13);
  static const surfaceHigh = Color(0xFF2A2518);
  static const border = Color(0xFF3A3322);
  static const gold = Color(0xFFE2C26B);
  static const goldDeep = Color(0xFF6B5416);
  static const ok = Color(0xFF59D49A);
  static const warn = Color(0xFFE8A64A);
  static const bad = Color(0xFFE5675F);
  static const muted = Color(0xFFB9B09A);

  /// The widest the content gets on a big screen; on a phone it simply fills the width.
  static const maxWidth = 860.0;

  static ThemeData theme() {
    final base = ColorScheme.fromSeed(seedColor: const Color(0xFFD4AF37), brightness: Brightness.dark).copyWith(
      primary: gold,
      onPrimary: const Color(0xFF241C05),
      primaryContainer: goldDeep,
      onPrimaryContainer: const Color(0xFFFFF1CC),
      surface: bg,
      surfaceContainerHighest: surfaceHigh,
      outline: border,
      outlineVariant: border,
    );
    final shape12 = RoundedRectangleBorder(borderRadius: BorderRadius.circular(12));
    return ThemeData(
      useMaterial3: true,
      colorScheme: base,
      scaffoldBackgroundColor: bg,
      appBarTheme: const AppBarTheme(
        backgroundColor: bg,
        surfaceTintColor: Colors.transparent,
        scrolledUnderElevation: 0,
        elevation: 0,
        titleTextStyle: TextStyle(fontSize: 20, fontWeight: FontWeight.w600, letterSpacing: 0.4, color: Colors.white),
      ),
      cardTheme: CardThemeData(
        color: surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: const EdgeInsets.symmetric(vertical: 6),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: const BorderSide(color: border)),
      ),
      dividerTheme: const DividerThemeData(color: border, space: 20),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surface,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: border)),
        enabledBorder:
            OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: border)),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: gold)),
        hintStyle: const TextStyle(color: muted),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
            shape: const StadiumBorder(), padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14)),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
            shape: const StadiumBorder(),
            side: const BorderSide(color: border),
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14)),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: surface,
        side: const BorderSide(color: border),
        shape: shape12,
        labelStyle: const TextStyle(fontSize: 13),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: const BorderSide(color: border)),
      ),
      bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      ),
      // The toast: it used to be the default light grey. Now it is a dark card with a gold edge, readable on the dark
      // screens, floating above the input instead of sitting on it.
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: surfaceHigh,
        contentTextStyle: const TextStyle(color: Colors.white, fontSize: 14, height: 1.35),
        actionTextColor: gold,
        elevation: 6,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: const BorderSide(color: gold, width: 0.8)),
      ),
      listTileTheme: const ListTileThemeData(iconColor: gold),
    );
  }
}

/// Keeps screen content in a readable column on a wide screen, centred; on a phone it changes nothing.
class Body extends StatelessWidget {
  final Widget child;
  const Body({super.key, required this.child});
  @override
  Widget build(BuildContext context) =>
      Align(alignment: Alignment.topCenter, child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: K.maxWidth), child: child));
}

/// A small coloured label ("Held", "Verified seller", "Sample data").
class Pill extends StatelessWidget {
  final String text;
  final Color color;
  final IconData? icon;
  const Pill(this.text, {super.key, this.color = K.muted, this.icon});
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
            color: color.withAlpha(36), borderRadius: BorderRadius.circular(20), border: Border.all(color: color.withAlpha(120))),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: 13, color: color), const SizedBox(width: 4)],
          Text(text, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: color)),
        ]),
      );
}
