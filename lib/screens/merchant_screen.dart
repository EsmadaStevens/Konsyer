import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:konsyer/core/auto_refresh.dart';
import 'package:konsyer/cards/dispute_card.dart';
import 'package:konsyer/core/api.dart';
import 'package:konsyer/widgets/nav_actions.dart';
import 'package:konsyer/widgets/photo_thumb.dart';

String? _orNull(String s) => s.trim().isEmpty ? null : s.trim();

/// "For businesses": sign up, then add listings with real times and capacity.
/// Nothing a business adds is visible to customers or the agent until Konsyer verifies it.
class MerchantScreen extends StatefulWidget {
  const MerchantScreen({super.key});
  @override
  State<MerchantScreen> createState() => _MerchantScreenState();
}

class _MerchantScreenState extends State<MerchantScreen> with AutoRefresh<MerchantScreen> {
  Map<String, dynamic>? merchant;
  List<Map<String, dynamic>> bookings = [];
  bool loading = true;
  bool busy = false;
  String? error;
  final _name = TextEditingController();
  final _area = TextEditingController();
  final _whatsapp = TextEditingController();
  final _social = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
    startAutoRefresh();
  }

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  @override
  Future<void> refresh() => _load();

  Future<void> _load() async {
    try {
      final j = await apiGet('/merchants?user_id=$userId');
      final list = (j['merchants'] as List).cast<Map<String, dynamic>>();
      var found = <Map<String, dynamic>>[];
      if (list.isNotEmpty) {
        try {
          final b = await apiGet('/merchants/${list.first['merchant_id']}/vouchers?user_id=$userId');
          found = (b['vouchers'] as List).cast<Map<String, dynamic>>();
        } catch (_) {}
      }
      if (mounted) {
        setState(() {
          merchant = list.isEmpty ? null : list.first;
          bookings = found;
          error = null;
          loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          error = '$e';
          loading = false;
        });
      }
    }
  }

  Future<void> _register() async {
    if (_name.text.trim().isEmpty) return _toast('Enter your business name.');
    setState(() => busy = true);
    try {
      await apiPost('/merchants', {
        'user_id': userId,
        'name': _name.text.trim(),
        'area': _orNull(_area.text),
        'whatsapp': _orNull(_whatsapp.text),
        'social_url': _orNull(_social.text),
      });
      await _load();
      _toast('Business registered. Add a listing next.');
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _addListing() async {
    final added = await Navigator.of(context).push<bool>(
        MaterialPageRoute(builder: (_) => ListingFormScreen(merchant: merchant!)));
    if (added == true) _load();
  }

  int _photoVersion = 0;

  /// A verified business can attach one picture of what it offers. Customers see it once the listing is verified.
  Future<void> _changePhoto(Map<String, dynamic> l) async {
    try {
      final x = await pickPhoto(ImageSource.gallery);
      if (x == null) return;
      final bytes = await x.readAsBytes();
      setState(() => busy = true);
      await apiUpload('/listings/${l['id']}/photo', {'user_id': '$userId'}, bytes, x.name);
      _photoVersion++;
      _toast('Picture added. Customers see it once the listing is verified.');
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _bookingAction(Map<String, dynamic> v, String verb, String done) async {
    setState(() => busy = true);
    try {
      await apiPost('/vouchers/${v['id']}/$verb', {'user_id': userId});
      _toast(done);
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _resolve(Map<String, dynamic> d) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Refund the customer?'),
        content: const Text('The held money goes back to the customer in full.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Not now')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Refund')),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => busy = true);
    try {
      await apiPost('/disputes/${d['id']}/resolve', {'user_id': userId, 'decision': 'refund'});
      _toast('Refunded. The customer got their money back.');
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  /// You cannot reject a claim against yourself. Contesting sends it to Konsyer with your reason.
  Future<void> _contest(Map<String, dynamic> d) async {
    final note = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Contest this claim?'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('Konsyer will review it and decide. The money stays held until then. Say briefly why you disagree.'),
          const SizedBox(height: 12),
          TextField(
            controller: note,
            maxLength: 500,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Your reason', border: OutlineInputBorder()),
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Send to Konsyer')),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => busy = true);
    try {
      await apiPost('/disputes/${d['id']}/contest', {'user_id': userId, 'note': note.text.trim()});
      _toast('Sent to Konsyer. The money stays held until they decide.');
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  /// Cannot make the appointment? Offer one of your other times. The customer accepts it or takes a full refund.
  Future<void> _offerNewTime(Map<String, dynamic> v) async {
    setState(() => busy = true);
    List<Map<String, dynamic>> slots = [];
    try {
      final j = await apiGet('/vouchers/${v['id']}/reschedule-options?user_id=$userId');
      slots = ((j['slots'] as List?) ?? []).cast<Map<String, dynamic>>();
    } catch (e) {
      _toast('$e');
      return;
    } finally {
      if (mounted) setState(() => busy = false);
    }
    if (!mounted) return;
    if (slots.isEmpty) return _toast('No other time with room is open. Add a time to the listing first.');
    final pick = await showDialog<int>(
      context: context,
      builder: (c) => SimpleDialog(
        title: const Text('Offer a new time'),
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(24, 0, 24, 8),
            child: Text('The customer can accept it or take a full refund. Their money stays held until they answer.'),
          ),
          for (final s in slots)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(c, (s['slot_id'] as num).toInt()),
              child: Text('${s['starts']}${s['ends'] == null ? '' : ' to ${s['ends']}'}'),
            ),
        ],
      ),
    );
    if (pick == null) return;
    setState(() => busy = true);
    try {
      await apiPost('/vouchers/${v['id']}/merchant-offer', {'user_id': userId, 'slot_id': pick});
      _toast('Offer sent. The customer can accept or take a full refund.');
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget _booking(Map<String, dynamic> v) {
    final t = Theme.of(context).textTheme;
    final held = v['state'] == 'held';
    final dispute = (v['dispute'] as Map?)?.cast<String, dynamic>();
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text((v['label'] ?? '').toString(), style: t.titleSmall)),
            Text(v['amount_display'].toString()),
          ]),
          Text('${held ? 'Held' : v['state']}${(v['starts'] ?? '').toString().isEmpty ? '' : '  -  ${v['starts']}'}',
              style: t.bodySmall),
          if (held)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Wrap(spacing: 8, runSpacing: 8, children: [
                FilledButton(
                  onPressed: (busy || v['can_confirm'] != true || v['offer'] != null)
                      ? null
                      : () => _bookingAction(v, 'merchant-confirm', 'Confirmed. Payment released (simulated).'),
                  child: const Text('Customer arrived'),
                ),
                if (v['can_offer_new_time'] == true)
                  OutlinedButton(
                    onPressed: busy ? null : () => _offerNewTime(v),
                    child: const Text('Offer a new time'),
                  ),
                OutlinedButton(
                  onPressed: busy
                      ? null
                      : () => _bookingAction(v, 'merchant-cancel', 'Cancelled. The customer was refunded in full.'),
                  child: const Text('Cancel booking'),
                ),
              ]),
            ),
          if (held && v['offer'] != null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text('You offered ${(v['offer'] as Map)['starts']}. Waiting for the customer to accept or take a refund.',
                  style: t.labelSmall),
            ),
          if (held && v['can_confirm'] != true)
            Text('You can confirm once the booking time has started.', style: t.labelSmall),
          if (dispute != null)
            DisputeCard(dispute,
                audience: DisputeAudience.business,
                loadPhoto: v['can_resolve'] == true
                    ? () => apiGetBytes('/disputes/${dispute['id']}/photo?user_id=$userId')
                    : null),
          if (v['can_resolve'] == true && dispute != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Wrap(spacing: 8, runSpacing: 8, children: [
                FilledButton(
                    onPressed: busy ? null : () => _resolve(dispute),
                    child: const Text('Refund customer')),
                OutlinedButton(
                    onPressed: busy ? null : () => _contest(dispute),
                    child: const Text('Contest')),
              ]),
            ),
        ]),
      ),
    );
  }

  Widget _signupForm() {
    final t = Theme.of(context).textTheme;
    return ListView(padding: const EdgeInsets.all(16), children: [
      Text('Get found and booked on Konsyer', style: t.titleLarge),
      const SizedBox(height: 4),
      Text('Register your business, add what you offer with real times and capacity. '
          'After Konsyer verifies you, your listings are trusted and shown first.', style: t.bodySmall),
      const SizedBox(height: 16),
      TextField(
          controller: _name,
          decoration: const InputDecoration(labelText: 'Business name', border: OutlineInputBorder())),
      const SizedBox(height: 12),
      TextField(
          controller: _area,
          decoration: const InputDecoration(labelText: 'Area (e.g. Kabulonga)', border: OutlineInputBorder())),
      const SizedBox(height: 12),
      TextField(
          controller: _whatsapp,
          keyboardType: TextInputType.phone,
          decoration: const InputDecoration(
              labelText: 'WhatsApp number', hintText: '0971234567', border: OutlineInputBorder())),
      const SizedBox(height: 12),
      TextField(
          controller: _social,
          keyboardType: TextInputType.url,
          decoration: const InputDecoration(
              labelText: 'Facebook or Instagram page link',
              hintText: 'https://www.facebook.com/yourpage',
              helperText: 'Helps us verify you. We only store the link.',
              border: OutlineInputBorder())),
      const SizedBox(height: 16),
      FilledButton(onPressed: busy ? null : _register, child: Text(busy ? 'Working...' : 'Register my business')),
    ]);
  }

  Widget _statusChip(String status, {bool paused = false}) {
    final live = status == 'verified' && !paused;
    final cs = Theme.of(context).colorScheme;
    return Chip(
      label: Text(paused ? 'Not offered' : (live ? 'Live' : 'Awaiting verification')),
      visualDensity: VisualDensity.compact,
      backgroundColor: live ? Colors.green.withAlpha(60) : cs.surfaceContainerHighest,
    );
  }

  Future<void> _addTimes(Map<String, dynamic> l) async {
    final added = await Navigator.of(context).push<bool>(
        MaterialPageRoute(builder: (_) => ListingFormScreen(merchant: merchant!, addTo: l)));
    if (added == true) _load();
  }

  /// Stop offering something. Customers stop seeing it; bookings already made stay and are honoured.
  Future<void> _togglePause(Map<String, dynamic> l) async {
    final paused = l['paused'] == 1 || l['paused'] == true;
    if (!paused) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text('Stop offering ${l['name']}?'),
          content: const Text('Customers will no longer see it or be able to book it. Bookings already made stay: '
              'serve them, offer another time, or cancel with a full refund. You can resume it any time.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep offering')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Stop offering')),
          ],
        ),
      );
      if (ok != true) return;
    }
    setState(() => busy = true);
    try {
      await apiPost('/listings/${l['id']}/pause', {'user_id': userId, 'paused': !paused});
      _toast(paused ? 'Resumed. Customers can see it again.' : 'Stopped. Customers no longer see it.');
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget _dashboard() {
    final m = merchant!;
    final t = Theme.of(context).textTheme;
    final verified = m['verified'] == true;
    final listings = (m['listings'] as List).cast<Map<String, dynamic>>();
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.all(16), children: [
        Text(m['name'].toString(), style: t.headlineSmall),
        if ((m['area'] ?? '').toString().isNotEmpty) Text(m['area'].toString()),
        const SizedBox(height: 8),
        Row(children: [
          Icon(verified ? Icons.verified : Icons.hourglass_top,
              color: verified ? Colors.greenAccent : Colors.amber, size: 18),
          const SizedBox(width: 6),
          Expanded(
              child: Text(verified
                  ? 'Verified: your live listings are shown first to customers.'
                  : 'Awaiting verification: your listings stay hidden from customers until Konsyer verifies you.')),
        ]),
        const SizedBox(height: 16),
        FilledButton.icon(
            onPressed: _addListing, icon: const Icon(Icons.add), label: const Text('Add a listing')),
        const SizedBox(height: 16),
        Text('Your listings', style: t.titleSmall),
        const Divider(),
        if (listings.isEmpty) const Text('No listings yet.'),
        for (final l in listings)
          Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: l['has_photo'] == true
                  ? PhotoThumb(
                      key: ValueKey('p${l['id']}-$_photoVersion'),
                      load: () => apiGetBytes('/listings/${l['id']}/photo?user_id=$userId'))
                  : null,
              title: Text(l['name'].toString()),
              subtitle: Text('${l['type']} - ${fmtK((l['price'] as num).toInt())}'),
              trailing: _statusChip(l['status'].toString(), paused: l['paused'] == 1 || l['paused'] == true),
            ),
            Wrap(spacing: 4, children: [
              TextButton(onPressed: busy ? null : () => _addTimes(l), child: const Text('Add times')),
              if (m['verified'] == true)
                TextButton(
                    onPressed: busy ? null : () => _changePhoto(l),
                    child: Text(l['has_photo'] == true ? 'Change photo' : 'Add photo')),
              TextButton(
                  onPressed: busy ? null : () => _togglePause(l),
                  child: Text(l['paused'] == 1 || l['paused'] == true ? 'Resume' : 'Stop offering')),
            ]),
            const Divider(height: 8),
          ]),
        const SizedBox(height: 16),
        Text('Customer bookings (payment held in escrow)', style: t.titleSmall),
        const Divider(),
        if (bookings.isEmpty) const Text('No bookings yet.'),
        for (final v in bookings) _booking(v),
        const SizedBox(height: 8),
        Text('Pull down to refresh the status.', style: t.labelSmall),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('For businesses'), actions: navActions(context, NavTarget.business)),
      body: loading
          ? const Center(child: CircularProgressIndicator())
          : error != null
              ? Center(child: Padding(padding: const EdgeInsets.all(16), child: Text('Could not load: $error')))
              : merchant == null
                  ? _signupForm()
                  : _dashboard(),
    );
  }
}

class _SlotFields {
  final date = TextEditingController();
  final start = TextEditingController();
  final end = TextEditingController();
  final cap = TextEditingController(text: '10');
}

/// One listing with one or more time slots. Capacity is the real number of places per slot.
class ListingFormScreen extends StatefulWidget {
  final Map<String, dynamic> merchant;
  /// When set, only new times are added to this existing listing (price and description stay locked).
  final Map<String, dynamic>? addTo;
  const ListingFormScreen({super.key, required this.merchant, this.addTo});
  @override
  State<ListingFormScreen> createState() => _ListingFormScreenState();
}

class _ListingFormScreenState extends State<ListingFormScreen> {
  String _type = 'activity';
  bool _alcohol = false;
  bool _busy = false;
  final _title = TextEditingController();
  final _desc = TextEditingController();
  final _area = TextEditingController();
  final _price = TextEditingController();
  final List<_SlotFields> _slots = [_SlotFields()];
  List<String> _tags = [];
  List<int>? _picture; // the listing picture (verified businesses only)
  String _pictureName = 'listing.jpg';
  bool get _canPicture => widget.merchant['verified'] == true;
  bool get _adding => widget.addTo != null;
  List<String> _check = [];
  String? _advice;

  @override
  void initState() {
    super.initState();
    _area.text = (widget.merchant['area'] ?? '').toString();
  }

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  /// The model only DRAFTS the form from a picture. You check and edit every field, then submit.
  /// Only a registered business can do this, and nothing is visible to customers until Konsyer verifies you.
  Future<void> _draft(String kind) async {
    try {
      final x = await pickPhoto(ImageSource.gallery, maxWidth: 2400, quality: 90);
      if (x == null) return;
      final bytes = await x.readAsBytes();
      setState(() => _busy = true);
      if (kind == 'photo' && _canPicture) {
        _picture = bytes; // the same photo can be the listing picture; the owner can remove it below
        _pictureName = x.name;
      }
      final j = await apiUpload('/merchants/${widget.merchant['merchant_id']}/draft-from-$kind',
          {'user_id': '$userId'}, bytes, x.name);
      final price = j['price_ngwee'];
      setState(() {
        _type = (j['listing_type'] ?? _type).toString();
        _title.text = (j['title'] ?? '').toString();
        _desc.text = (j['description'] ?? '').toString();
        _alcohol = j['serves_alcohol'] == true;
        _tags = ((j['tags'] as List?) ?? []).map((e) => '$e').toList();
        if (price is int) {
          _price.text = price % 100 == 0 ? '${price ~/ 100}' : (price / 100).toStringAsFixed(2);
        }
        final d = (j['date'] ?? '').toString();
        final st = (j['start_time'] ?? '').toString();
        if (d.isNotEmpty) _slots.first.date.text = d;
        if (st.isNotEmpty) _slots.first.start.text = st;
        _check = ((j['needs_confirmation'] as List?) ?? []).map((e) => '$e').toList();
        _advice = j['advice']?.toString();
      });
      _toast('Drafted from your ${kind == 'flyer' ? 'flyer' : 'photo'}. Check every field before you submit.');
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    if (!_adding && _title.text.trim().isEmpty) return _toast('Enter a title.');
    final price = double.tryParse(_price.text.trim().replaceAll(',', ''));
    if (!_adding && (price == null || price <= 0)) return _toast('Price must be a number in kwacha, more than zero.');
    final slots = <Map<String, dynamic>>[];
    for (var i = 0; i < _slots.length; i++) {
      final s = _slots[i];
      final cap = int.tryParse(s.cap.text.trim());
      if (s.date.text.trim().isEmpty || s.start.text.trim().isEmpty || cap == null) {
        return _toast('Time ${i + 1}: enter a date, a start time and a number of places.');
      }
      slots.add({
        'date': s.date.text.trim(),
        'start_time': s.start.text.trim(),
        'end_time': _orNull(s.end.text),
        'capacity': cap,
      });
    }
    setState(() => _busy = true);
    try {
      if (_adding) {
        await apiPost('/listings/${widget.addTo!['id']}/slots', {'user_id': userId, 'slots': slots});
        _toast('Times added.');
        if (mounted) Navigator.of(context).pop(true);
        return;
      }
      final j = await apiPost('/merchants/${widget.merchant['merchant_id']}/listings', {
        'user_id': userId,
        'title': _title.text.trim(),
        'listing_type': _type,
        'description': _orNull(_desc.text),
        'area': _orNull(_area.text),
        'price_ngwee': (price! * 100).round(),
        'serves_alcohol': _alcohol,
        'tags': _tags,
        'slots': slots,
      });
      final pic = _picture;
      var msg = j['message']?.toString() ?? 'Submitted.';
      if (pic != null && _canPicture && j['listing_id'] != null) {
        try {
          await apiUpload('/listings/${j['listing_id']}/photo', {'user_id': '$userId'}, pic, _pictureName);
        } catch (e) {
          msg = '$msg The picture was not saved: $e';
        }
      }
      _toast(msg);
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  static const _maxSlots = 20;

  String _ymd(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  /// Copies one time onto the following days or weeks so a seller does not retype it. Every copy stays editable.
  Future<void> _repeat(int i) async {
    final src = _slots[i];
    final base = DateTime.tryParse(src.date.text.trim());
    if (base == null || src.start.text.trim().isEmpty) {
      return _toast('Fill in this time\'s date (YYYY-MM-DD) and start first, then repeat it.');
    }
    final room = _maxSlots - _slots.length;
    if (room <= 0) return _toast('You can add up to $_maxSlots times.');
    final count = TextEditingController(text: room < 6 ? '$room' : '6');
    var weekly = false;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setD) => AlertDialog(
          title: const Text('Repeat this time'),
          content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            RadioGroup<bool>(
              groupValue: weekly,
              onChanged: (v) => setD(() => weekly = v ?? false),
              child: const Column(children: [
                RadioListTile<bool>(contentPadding: EdgeInsets.zero, value: false, title: Text('Every day')),
                RadioListTile<bool>(contentPadding: EdgeInsets.zero, value: true, title: Text('Every week')),
              ]),
            ),
            TextField(
                controller: count,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(
                    labelText: 'How many more ${weekly ? 'weeks' : 'days'}? (up to $room)',
                    border: const OutlineInputBorder())),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Add times')),
          ],
        ),
      ),
    );
    final n = int.tryParse(count.text.trim()) ?? 0;
    count.dispose();
    if (ok != true || n < 1) return;
    final add = n > room ? room : n;
    setState(() {
      var at = i;
      for (var k = 1; k <= add; k++) {
        final f = _SlotFields();
        f.date.text = _ymd(base.add(Duration(days: (weekly ? 7 : 1) * k)));
        f.start.text = src.start.text;
        f.end.text = src.end.text;
        f.cap.text = src.cap.text;
        _slots.insert(++at, f);
      }
    });
    _toast('Added $add times. Change or remove any of them.');
  }

  Widget _slotRow(int i) {
    final s = _slots[i];
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(children: [
          Row(children: [
            Expanded(child: Text('Time ${i + 1}', style: Theme.of(context).textTheme.titleSmall)),
            TextButton.icon(
                onPressed: () => _repeat(i), icon: const Icon(Icons.repeat, size: 18), label: const Text('Repeat')),
            if (_slots.length > 1)
              IconButton(
                  tooltip: 'Remove',
                  icon: const Icon(Icons.close),
                  onPressed: () => setState(() => _slots.removeAt(i))),
          ]),
          Row(children: [
            Expanded(
                child: TextField(
                    controller: s.date,
                    decoration: const InputDecoration(
                        labelText: 'Date', hintText: 'YYYY-MM-DD', border: OutlineInputBorder()))),
            const SizedBox(width: 8),
            Expanded(
                child: TextField(
                    controller: s.start,
                    decoration: const InputDecoration(
                        labelText: 'Start', hintText: 'HH:MM', border: OutlineInputBorder()))),
          ]),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(
                child: TextField(
                    controller: s.end,
                    decoration: const InputDecoration(
                        labelText: 'End (optional)', hintText: 'HH:MM', border: OutlineInputBorder()))),
            const SizedBox(width: 8),
            Expanded(
                child: TextField(
                    controller: s.cap,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                        labelText: 'Places available', border: OutlineInputBorder()))),
          ]),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(_adding ? 'Add times: ${widget.addTo!['name']}' : 'Add a listing')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        if (!_adding) ...[
        Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Fill it in from a picture', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 4),
              Text('A Meta vision model drafts the form. You check and edit it, and Konsyer verifies it before customers see it.',
                  style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(height: 8),
              Wrap(spacing: 8, runSpacing: 8, children: [
                OutlinedButton.icon(
                    onPressed: _busy ? null : () => _draft('flyer'),
                    icon: const Icon(Icons.description_outlined),
                    label: const Text('From a flyer')),
                OutlinedButton.icon(
                    onPressed: _busy ? null : () => _draft('photo'),
                    icon: const Icon(Icons.photo_camera_outlined),
                    label: const Text('From a product photo')),
              ]),
              if (_check.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text('Please check: ${_check.join(', ')}.', style: Theme.of(context).textTheme.bodySmall),
                ),
              if (_advice != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(_advice!,
                      style: TextStyle(color: Theme.of(context).colorScheme.error, fontSize: 12)),
                ),
            ]),
          ),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          initialValue: _type,
          decoration: const InputDecoration(labelText: 'Type', border: OutlineInputBorder()),
          items: const [
            DropdownMenuItem(value: 'activity', child: Text('Activity')),
            DropdownMenuItem(value: 'event', child: Text('Event')),
            DropdownMenuItem(value: 'dining', child: Text('Dining')),
            DropdownMenuItem(value: 'service', child: Text('Service (e.g. hair, nails)')),
          ],
          onChanged: (v) => setState(() => _type = v ?? 'activity'),
        ),
        const SizedBox(height: 12),
        TextField(
            controller: _title,
            decoration: const InputDecoration(labelText: 'Title', border: OutlineInputBorder())),
        const SizedBox(height: 12),
        TextField(
            controller: _desc,
            maxLines: 3,
            decoration: const InputDecoration(
                labelText: 'Description',
                helperText: 'Be specific (colour, shape or size, finish): customers are protected by a photo check.',
                border: OutlineInputBorder())),
        const SizedBox(height: 12),
        TextField(
            controller: _area,
            decoration: const InputDecoration(labelText: 'Area', border: OutlineInputBorder())),
        const SizedBox(height: 12),
        TextField(
            controller: _price,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
                labelText: 'Price per person (kwacha)', prefixText: 'K ', border: OutlineInputBorder())),
        if (_canPicture)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(children: [
              if (_picture != null)
                ClipRRect(
                    borderRadius: BorderRadius.circular(6),
                    child: Image.memory(Uint8List.fromList(_picture!), width: 56, height: 56, fit: BoxFit.cover)),
              if (_picture != null) const SizedBox(width: 10),
              Expanded(
                  child: Text(_picture == null
                      ? 'Listing picture (optional). Customers see it once Konsyer verifies the listing.'
                      : 'This picture will show on your listing.',
                      style: Theme.of(context).textTheme.bodySmall)),
              TextButton(
                  onPressed: _busy
                      ? null
                      : () async {
                          if (_picture != null) return setState(() => _picture = null);
                          final x = await pickPhoto(ImageSource.gallery);
                          if (x == null) return;
                          final b = await x.readAsBytes();
                          setState(() {
                            _picture = b;
                            _pictureName = x.name;
                          });
                        },
                  child: Text(_picture == null ? 'Add picture' : 'Remove')),
            ]),
          ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Alcohol is served'),
          subtitle: const Text('Konsyer will offer customers a ride home.'),
          value: _alcohol,
          onChanged: (v) => setState(() => _alcohol = v),
        ),
        ],
        const SizedBox(height: 8),
        for (var i = 0; i < _slots.length; i++) _slotRow(i),
        if (_slots.length < _maxSlots)
          TextButton.icon(
              onPressed: () => setState(() => _slots.add(_SlotFields())),
              icon: const Icon(Icons.add),
              label: const Text('Add another time')),
        const SizedBox(height: 12),
        FilledButton(
            onPressed: _busy ? null : _submit,
            child: Text(_busy ? 'Working...' : (_adding ? 'Add these times' : 'Submit listing'))),
        const SizedBox(height: 8),
        Text('Your listing appears to customers after Konsyer verifies your business.',
            style: Theme.of(context).textTheme.labelSmall),
      ]),
    );
  }
}
