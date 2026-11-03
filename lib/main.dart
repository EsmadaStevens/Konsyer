import 'package:flutter/material.dart';
import 'package:konsyer/core/theme.dart';
import 'package:konsyer/screens/admin_screen.dart';
import 'package:konsyer/screens/chat_screen.dart';

void main() => runApp(const ConciergeApp());

class ConciergeApp extends StatelessWidget {
  const ConciergeApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Konsyer',
        debugShowCheckedModeBanner: false,
        theme: K.theme(),
        home: const ChatScreen(),
        // The admin console is a separate address (open /#/admin in its own tab), not part of the customer app.
        routes: {'/admin': (_) => const AdminScreen()},
      );
}
