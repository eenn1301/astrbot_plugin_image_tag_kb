    def _find_images(self, query: str) -> List[str]:
        q = query.lower().strip()
        if not q:
            return []

        # 1. 精确匹配优先，直接返回
        if q in self.tag_to_image:
            return [self.tag_to_image[q]]

        # 2. 子串匹配，要求标签长度达到阈值，避免短标签误吞长词
        #    例如标签「冰」不应命中查询「冰窖」
        min_len = int(self.config.get("min_tag_length", 2))
        matched: List[tuple] = []
        for tag, path in self.tag_to_image.items():
            if tag in q:
                # 标签是查询的子串（用户说了更长的话）
                if len(tag) >= min_len:
                    matched.append((len(tag), tag, path))
            elif q in tag:
                # 查询是标签的子串（用户说了更短的话，如「冰」查「冰淇淋」）
                if len(q) >= min_len:
                    matched.append((len(tag), tag, path))

        if not matched:
            return []

        # 3. 优先返回最长（最具体）的匹配；同长度全部返回
        matched.sort(key=lambda x: -x[0])
        best_len = matched[0][0]
        return [path for length, tag, path in matched if length == best_len]