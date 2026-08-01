[File: string_utils.rs] [Symbol: format Vector<u8> hex fast-path vs generic Vector path] The code special-cases 'MoveTypeLayout::Vector(ty) if ty is U8' to hex-encode the raw bytes ('write!(out, \
