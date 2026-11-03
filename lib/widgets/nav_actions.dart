import 'package:flutter/material.dart';
import 'package:konsyer/core/theme.dart';
import 'package:konsyer/screens/activity_screen.dart';
import 'package:konsyer/screens/merchant_screen.dart';
import 'package:konsyer/screens/wallet_screen.dart';

/// The four places a customer moves between. The same buttons sit in every screen's app bar, so you can go from the
/// business screen straight to Activity without going back first.
enum NavTarget { chat, business, activity, wallet }

List<Widget> navActions(BuildContext context, NavTarget current, {int walletBadge = 0, VoidCallback? onReturn}) {
  Future<void> go(NavTarget to) async {
    if (to == current) return;
    final nav = Navigator.of(context);
    if (to == NavTarget.chat) {
      nav.popUntil((r) => r.isFirst);
      return;
    }
    final route = MaterialPageRoute<void>(
      builder: (_) => switch (to) {
        NavTarget.business => const MerchantScreen(),
        NavTarget.activity => const ActivityScreen(),
        _ => const WalletScreen(),
      },
    );
    if (current == NavTarget.chat) {
      await nav.push(route);
      onReturn?.call();
    } else {
      nav.pushReplacement(route); // swap screens, so Back still returns to the chat
    }
  }

  Widget item(NavTarget t, IconData icon, String tip, {Widget? child}) => IconButton(
        tooltip: tip,
        color: t == current ? K.gold : null,
        icon: child ?? Icon(icon),
        onPressed: () => go(t),
      );

  return [
    item(NavTarget.chat, Icons.chat_bubble_outline, 'Chat'),
    item(NavTarget.business, Icons.storefront_outlined, 'For businesses'),
    item(NavTarget.activity, Icons.timeline_outlined, 'Activity: what the models did'),
    item(NavTarget.wallet, Icons.account_balance_wallet_outlined, 'Wallet',
        child: Badge(
            isLabelVisible: walletBadge > 0,
            label: Text('$walletBadge'),
            child: const Icon(Icons.account_balance_wallet_outlined))),
    const SizedBox(width: 4),
  ];
}
