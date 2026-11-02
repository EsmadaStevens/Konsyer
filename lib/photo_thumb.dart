import 'dart:typed_data';
import 'package:flutter/material.dart';

/// A small picture loaded with a request (so it can carry the owner's id or the admin key).
class PhotoThumb extends StatelessWidget {
  final Future<Uint8List> Function() load;
  final double size;
  const PhotoThumb({super.key, required this.load, this.size = 48});
  @override
  Widget build(BuildContext context) => SizedBox(
        width: size,
        height: size,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: FutureBuilder<Uint8List>(
            future: load(),
            builder: (c, snap) => snap.hasData
                ? Image.memory(snap.data!, fit: BoxFit.cover)
                : Container(color: Theme.of(c).colorScheme.surfaceContainerHighest),
          ),
        ),
      );
}
