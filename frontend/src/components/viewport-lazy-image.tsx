// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from "react";
import type { ImgHTMLAttributes } from "react";

type ViewportLazyImageProps = Omit<
  ImgHTMLAttributes<HTMLImageElement>,
  "src"
> & {
  src: string;
  rootMargin?: string;
};

const MIN_VISIBLE_RATIO = 0.01;

/** Keep media URLs out of the DOM until their card approaches the viewport. */
export function ViewportLazyImage({
  src,
  rootMargin = "320px",
  loading = "lazy",
  ...props
}: ViewportLazyImageProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [revealed, setRevealed] = useState(loading === "eager");

  useEffect(() => {
    const image = imageRef.current;
    if (!image || !src || revealed) return;
    if (loading === "eager" || typeof IntersectionObserver === "undefined") {
      setRevealed(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (
          !entries.some(
            (entry) =>
              entry.isIntersecting &&
              entry.intersectionRatio >= MIN_VISIBLE_RATIO,
          )
        ) {
          return;
        }
        setRevealed(true);
        observer.disconnect();
      },
      {
        root: null,
        rootMargin,
        threshold: MIN_VISIBLE_RATIO,
      },
    );
    observer.observe(image);
    return () => observer.disconnect();
  }, [loading, revealed, rootMargin, src]);

  return (
    <img
      ref={imageRef}
      {...props}
      src={revealed ? src : undefined}
      loading={loading}
      decoding={props.decoding ?? "async"}
    />
  );
}
