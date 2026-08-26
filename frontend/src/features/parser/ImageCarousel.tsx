import { useEffect, useState } from 'react';
import { Tag } from 'antd';
import { Swiper, SwiperSlide } from 'swiper/react';
import { A11y, Keyboard, Navigation, Pagination } from 'swiper/modules';
import 'swiper/css';
import 'swiper/css/navigation';
import 'swiper/css/pagination';

/** Tiny inline SVG placeholder shown when a slide fails to load or is absent. */
export const COVER_FALLBACK =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="170">' +
      '<rect width="100%" height="100%" fill="#f0f0f0"/>' +
      '<text x="50%" y="50%" fill="#bfbfbf" font-size="14" text-anchor="middle" dominant-baseline="middle">Koi Fetch</text>' +
      '</svg>',
  );

export interface ImageCarouselProps {
  /** Album image URLs (caller guarantees at least one). */
  images: string[];
  title: string;
  /** Fired with the un-looped active index whenever the slide changes. */
  onIndexChange?: (index: number) => void;
  testId?: string;
}

/**
 * Swiper carousel for image albums (图集/动图) on the result grid.
 *
 * Navigation arrows, clickable pagination and keyboard support; lazy-loaded
 * slides fall back to COVER_FALLBACK per-slide. The active index is reported
 * through `onIndexChange` so the card's [下载当前] button tracks what the user
 * is looking at (`realIndex` is used so loop mode never reports a clone).
 */
export function ImageCarousel({
  images,
  title,
  onIndexChange,
  testId,
}: ImageCarouselProps): JSX.Element {
  const [active, setActive] = useState(0);

  // A replaced/shorter album must not leave the counter past its end.
  useEffect(() => {
    setActive(0);
  }, [images]);

  return (
    <div className="image-carousel" data-testid={testId}>
      <Swiper
        modules={[A11y, Navigation, Pagination, Keyboard]}
        navigation
        pagination={{ clickable: true }}
        keyboard={{ enabled: true }}
        loop={images.length > 1}
        onSlideChange={(swiper) => {
          const index = swiper.realIndex;
          setActive(index);
          onIndexChange?.(index);
        }}
      >
        {images.map((url, index) => (
          <SwiperSlide key={`${url}-${index}`}>
            <img
              src={url}
              alt={`${title} ${index + 1}`}
              loading="lazy"
              className="image-carousel-slide"
              onError={(event) => {
                const img = event.currentTarget;
                if (img.src !== COVER_FALLBACK) img.src = COVER_FALLBACK;
              }}
            />
          </SwiperSlide>
        ))}
      </Swiper>
      <Tag className="image-carousel-count" data-testid={`${testId}-count`}>
        {active + 1} / {images.length}
      </Tag>
    </div>
  );
}
