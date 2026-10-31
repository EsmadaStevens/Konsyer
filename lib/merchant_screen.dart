import 'package:flutter/material.dart';
import 'api.dart';

String? _orNull(String s) => s.trim().isEmpty ? null : s.trim();

/// "For businesses": sign up, then add listings with real times and capacity.
/// Nothing a business adds is visible to customers or the agent until Konsyer verifies it.
class MerchantScreen extends StatefulWidget {
  const MerchantScreen({super.key});
  @override
  State<MerchantScreen> createState() => _MerchantScreenState();
}

class _MerchantScreenState extends State<MerchantScreen> {
  Map<String, dynamic>? merchant;
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
  }

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  Future<void> _load() async {
    try {
      final j = await apiGet('/merchants?user_id=$userId');
      final list = (j['merchants'] as List).cast<Map<String, dynamic>>();
      if (mounted) {
        setState(() {
          merchant = list.isEmpty ? null : list.first;
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

  Widget _statusChip(String status) {
    final live = status == 'verified';
    final cs = Theme.of(context).colorScheme;
    return Chip(
      label: Text(live ? 'Live' : 'Awaiting verification'),
      visualDensity: VisualDensity.compact,
      backgroundColor: live ? Colors.green.withAlpha(60) : cs.surfaceContainerHighest,
    );
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
          ListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(l['name'].toString()),
            subtitle: Text('${l['type']} - ${fmtK((l['price'] as num).toInt())}'),
            trailing: _statusChip(l['status'].toString()),
          ),
        const SizedBox(height: 8),
        Text('Pull down to refresh the status.', style: t.labelSmall),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('For businesses')),
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
  const ListingFormScreen({super.key, required this.merchant});
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

  @override
  void initState() {
    super.initState();
    _area.text = (widget.merchant['area'] ?? '').toString();
  }

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  Future<void> _submit() async {
    if (_title.text.trim().isEmpty) return _toast('Enter a title.');
    final price = double.tryParse(_price.text.trim().replaceAll(',', ''));
    if (price == null || price <= 0) return _toast('Price must be a number in kwacha, more than zero.');
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
      final j = await apiPost('/merchants/${widget.merchant['merchant_id']}/listings', {
        'user_id': userId,
        'title': _title.text.trim(),
        'listing_type': _type,
        'description': _orNull(_desc.text),
        'area': _orNull(_area.text),
        'price_ngwee': (price * 100).round(),
        'serves_alcohol': _alcohol,
        'tags': <String>[],
        'slots': slots,
      });
      _toast(j['message']?.toString() ?? 'Submitted.');
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
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
      appBar: AppBar(title: const Text('Add a listing')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
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
            maxLines: 2,
            decoration: const InputDecoration(labelText: 'Description (optional)', border: OutlineInputBorder())),
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
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Alcohol is served'),
          subtitle: const Text('Konsyer will offer customers a ride home.'),
          value: _alcohol,
          onChanged: (v) => setState(() => _alcohol = v),
        ),
        const SizedBox(height: 8),
        for (var i = 0; i < _slots.length; i++) _slotRow(i),
        if (_slots.length < 20)
          TextButton.icon(
              onPressed: () => setState(() => _slots.add(_SlotFields())),
              icon: const Icon(Icons.add),
              label: const Text('Add another time')),
        const SizedBox(height: 12),
        FilledButton(onPressed: _busy ? null : _submit, child: Text(_busy ? 'Working...' : 'Submit listing')),
        const SizedBox(height: 8),
        Text('Your listing appears to customers after Konsyer verifies your business.',
            style: Theme.of(context).textTheme.labelSmall),
      ]),
    );
  }
}