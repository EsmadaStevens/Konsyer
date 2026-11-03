import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

/// A drafted message to a seller. The app never sends anything: the user taps a link
/// (WhatsApp, Messenger, Instagram, call, source page) or copies the text and sends it themselves.
/// Only https and tel links are opened.
class ContactCard extends StatefulWidget {
  final Map<String, dynamic> c;
  const ContactCard(this.c, {super.key});
  @override
  State<ContactCard> createState() => _ContactCardState();
}

class _ContactCardState extends State<ContactCard> {
  bool _invite = false;
  Map<String, dynamic> get c => widget.c;

  static IconData _icon(String kind) {
    switch (kind) {
      case 'whatsapp':
        return Icons.chat_outlined;
      case 'messenger':
        return Icons.forum_outlined;
      case 'instagram':
        return Icons.camera_alt_outlined;
      case 'call':
        return Icons.call_outlined;
      default:
        return Icons.open_in_new;
    }
  }

  void _toast(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _open(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null || !(uri.scheme == 'https' || uri.scheme == 'tel')) {
      _toast('That link is not allowed.');
      return;
    }
    try {
      final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!ok && context.mounted) _toast('Could not open the link.');
    } catch (_) {
      if (context.mounted) _toast('Could not open the link.');
    }
  }

  Future<void> _copy(String text) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (context.mounted) _toast('Message copied');
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = Theme.of(context).textTheme;
    final verified = c['verified'] == true;
    final price = (c['price_text'] ?? '').toString();
    final priceFor = (c['price_for'] ?? '').toString();
    final phone = (c['phone'] ?? '').toString();
    final source = (c['source_url'] ?? '').toString();
    final hasInvite = (c['invite_message'] ?? '').toString().isNotEmpty;
    final inviting = _invite && hasInvite;
    final message = (inviting ? c['invite_message'] : c['message'] ?? '').toString();
    final channels = ((inviting ? c['invite_channels'] : c['channels']) as List? ?? []).cast<Map<String, dynamic>>();

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(inviting ? 'Invite ${c['name']} to Konsyer' : 'Contact ${c['name']}', style: t.titleMedium),
          const SizedBox(height: 2),
          Text(
            verified
                ? 'Verified seller'
                : 'Unverified web result: check it before you send anything',
            style: t.labelSmall?.copyWith(color: verified ? Colors.greenAccent : cs.error),
          ),
          if (c['sample_data'] == true) Text('Sample data', style: t.labelSmall),
          const SizedBox(height: 6),
          if (price.isNotEmpty)
            Text('Price on the page: $price '
                '(${priceFor.isEmpty ? 'what it covers is not stated, confirm with the seller' : priceFor})')
          else if (source.isNotEmpty)
            const Text('Price: not listed, ask the seller'),
          if (phone.isNotEmpty) Text('Number: +$phone'),
          if (c['corroborated'] == true)
            Text('The same number was found on more than one site', style: t.labelSmall),
          if (source.isNotEmpty)
            Text('Web directories can be out of date: check before you go.', style: t.labelSmall),
          const Divider(),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: cs.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(10),
            ),
            child: SelectableText(message),
          ),
          const SizedBox(height: 8),
          Wrap(spacing: 8, runSpacing: 8, children: [
            OutlinedButton.icon(
              onPressed: () => _copy(message),
              icon: const Icon(Icons.copy_outlined),
              label: const Text('Copy message'),
            ),
            for (final ch in channels)
              FilledButton.tonalIcon(
                onPressed: () => _open((ch['url'] ?? '').toString()),
                icon: Icon(_icon((ch['kind'] ?? '').toString())),
                label: Text((ch['label'] ?? 'Open').toString()),
              ),
          ]),
          if (hasInvite)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: TextButton.icon(
                onPressed: () => setState(() => _invite = !_invite),
                icon: Icon(inviting ? Icons.arrow_back : Icons.storefront_outlined),
                label: Text(inviting ? 'Back to my message' : 'Invite this business to Konsyer'),
              ),
            ),
          const SizedBox(height: 4),
          Text('Nothing has been sent. You tap send yourself.', style: t.labelSmall),
          if (!verified)
            Text('Konsyer does not handle payment for sellers outside the app. You and the seller arrange it directly.',
                style: t.labelSmall),
        ]),
      ),
    );
  }
}