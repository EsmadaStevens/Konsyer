import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'api.dart';

class FlyerScreen extends StatefulWidget {
  const FlyerScreen({super.key});
  @override
  State<FlyerScreen> createState() => _FlyerScreenState();
}

class _FlyerScreenState extends State<FlyerScreen> {
  final _picker = ImagePicker();
  bool _busy = false;
  String _kind = 'flyer';
  int? _flyerId;
  List<String> _needs = [];
  List<String> _performers = [];
  List<Map<String, dynamic>> _tiers = [];
  String _type = 'event';
  bool _alcohol = false;
  final _title = TextEditingController();
  final _date = TextEditingController();
  final _time = TextEditingController();
  final _venue = TextEditingController();
  final _area = TextEditingController();
  final _price = TextEditingController();
  final _phone = TextEditingController();

  void _toast(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  String _kwacha(int ngwee) =>
      ngwee % 100 == 0 ? '${ngwee ~/ 100}' : (ngwee / 100).toStringAsFixed(2);

  Future<void> _pick(ImageSource src) async {
    try {
      final x = await _picker.pickImage(source: src, maxWidth: 2400, imageQuality: 90);
      if (x == null) return;
      final bytes = await x.readAsBytes();
      setState(() => _busy = true);
      final j = await apiUpload('/flyers', {'user_id': '$userId', 'kind': _kind}, bytes, x.name);
      _fill(j);
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _fill(Map<String, dynamic> j) {
    final d = (j['draft'] as Map).cast<String, dynamic>();
    final min = d['price_min'];
    setState(() {
      _flyerId = j['flyer_id'] as int;
      _needs = ((d['needs_confirmation'] as List?) ?? []).map((e) => '$e').toList();
      _performers = ((d['performers'] as List?) ?? []).map((e) => '$e').toList();
      _tiers = ((d['prices'] as List?) ?? []).cast<Map<String, dynamic>>();
      _type = (d['listing_type'] ?? 'event').toString();
      _alcohol = d['serves_alcohol'] == true;
      _title.text = (d['title'] ?? '').toString();
      _date.text = (d['date'] ?? '').toString();
      _time.text = (d['start_time'] ?? '').toString();
      _venue.text = (d['venue'] ?? '').toString();
      _area.text = '';
      _phone.text = (d['contact_phone'] ?? '').toString();
      _price.text = min is int ? _kwacha(min) : '';
    });
  }

  Future<void> _confirm() async {
    int? ngwee;
    if (_price.text.trim().isNotEmpty) {
      final v = double.tryParse(_price.text.trim().replaceAll(',', ''));
      if (v == null) return _toast('Price must be a number in kwacha.');
      ngwee = (v * 100).round();
    }
    setState(() => _busy = true);
    try {
      final j = await apiPost('/flyers/$_flyerId/confirm', {
        'user_id': userId,
        'title': _title.text.trim(),
        'listing_type': _type,
        'date': _date.text.trim().isEmpty ? null : _date.text.trim(),
        'start_time': _time.text.trim().isEmpty ? null : _time.text.trim(),
        'venue': _venue.text.trim(),
        'area': _area.text.trim(),
        'price_ngwee': ngwee,
        'serves_alcohol': _alcohol,
        'contact_phone': _phone.text.trim(),
        'performers': _performers,
      });
      if (mounted) Navigator.of(context).pop(j['message']?.toString() ?? 'Added.');
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  InputDecoration _dec(String label, {bool warn = false, String? hint}) => InputDecoration(
        labelText: label,
        hintText: hint,
        border: const OutlineInputBorder(),
        filled: warn,
        fillColor: warn ? Colors.amber.withAlpha(40) : null,
        helperText: warn ? 'Please check this' : null,
      );

  Widget _field(TextEditingController c, String label, {bool warn = false, String? hint}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextField(controller: c, decoration: _dec(label, warn: warn, hint: hint)),
      );

  Widget _picker_() => ListView(padding: const EdgeInsets.all(16), children: [
        const Text('Snap a flyer or upload a screenshot. Konsyer reads it, you check it, and it becomes a listing.'),
        const SizedBox(height: 16),
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'flyer', label: Text('Flyer')),
            ButtonSegment(value: 'screenshot', label: Text('Screenshot')),
          ],
          selected: {_kind},
          onSelectionChanged: (s) => setState(() => _kind = s.first),
        ),
        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: _busy ? null : () => _pick(ImageSource.gallery),
          icon: const Icon(Icons.photo_library_outlined),
          label: const Text('Choose a photo'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: _busy ? null : () => _pick(ImageSource.camera),
          icon: const Icon(Icons.photo_camera_outlined),
          label: const Text('Take a photo'),
        ),
        if (_busy) ...[
          const SizedBox(height: 24),
          const LinearProgressIndicator(),
          const SizedBox(height: 8),
          const Text('Reading the image. This takes 10 to 15 seconds.'),
        ],
      ]);

  Widget _review() => ListView(padding: const EdgeInsets.all(16), children: [
        const Text('Check the details. Highlighted fields were unclear on the image.'),
        const SizedBox(height: 12),
        _field(_title, 'Title', warn: _needs.contains('title')),
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: DropdownButtonFormField<String>(
            value: _type,
            decoration: _dec('Type'),
            items: const ['event', 'activity', 'dining', 'service']
                .map((t) => DropdownMenuItem(value: t, child: Text(t)))
                .toList(),
            onChanged: (v) => setState(() => _type = v ?? 'event'),
          ),
        ),
        if (_type != 'service') ...[
          _field(_date, 'Date', warn: _needs.contains('date'), hint: 'YYYY-MM-DD'),
          _field(_time, 'Start time (Lusaka)', warn: _needs.contains('start_time'), hint: 'HH:MM, e.g. 20:00'),
        ],
        _field(_venue, 'Venue', warn: _needs.contains('venue') || _needs.contains('contact_or_location')),
        _field(_area, 'Area (optional)'),
        if (_tiers.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Wrap(spacing: 8, children: [
              for (final t in _tiers)
                ActionChip(
                  label: Text('${t['label'] ?? 'Ticket'} ${fmtK((t['amount'] as num).toInt())}'),
                  onPressed: () => setState(() => _price.text = _kwacha((t['amount'] as num).toInt())),
                ),
            ]),
          ),
        _field(_price, 'Price per person (K)', warn: _needs.contains('price')),
        _field(_phone, 'Booking phone / WhatsApp',
            warn: _needs.contains('contact_or_location')),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Drinks are served'),
          value: _alcohol,
          onChanged: (v) => setState(() => _alcohol = v),
        ),
        if (_type == 'event')
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text(_performers.isEmpty ? 'Lineup not listed' : 'Lineup: ${_performers.join(', ')}'),
          ),
        const SizedBox(height: 8),
        FilledButton(onPressed: _busy ? null : _confirm, child: Text(_busy ? 'Saving...' : 'Add listing')),
        const SizedBox(height: 4),
        const Text('Listings from flyers are unverified, so paying for them always needs your approval.',
            style: TextStyle(fontSize: 12)),
      ]);

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Add a flyer')),
        body: _flyerId == null ? _picker_() : _review(),
      );
}