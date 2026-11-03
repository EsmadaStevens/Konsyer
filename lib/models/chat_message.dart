class Msg {
  final bool fromUser;
  final String text;
  final List<Map<String, dynamic>> cards;
  final bool suggestWeb; // offer a one-tap "Also search the web"
  Msg(this.fromUser, this.text, [this.cards = const [], this.suggestWeb = false]);
}
