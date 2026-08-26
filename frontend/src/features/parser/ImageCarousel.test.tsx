import { describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ImageCarousel } from './ImageCarousel';

// swiper/react needs layout APIs jsdom cannot fully provide — mock the
// Swiper surface the component uses and drive onSlideChange directly.
let slideChangeHandler: ((swiper: { realIndex: number }) => void) | null = null;

vi.mock('swiper/react', () => ({
  Swiper: ({ children, onSlideChange }: { children: ReactNode; onSlideChange?: (s: { realIndex: number }) => void }) => {
    slideChangeHandler = onSlideChange ?? null;
    return <div data-testid="mock-swiper">{children}</div>;
  },
  SwiperSlide: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

describe('ImageCarousel', () => {
  it('renders one slide per image', () => {
    render(
      <ImageCarousel
        images={['https://cdn.example.com/1.jpg', 'https://cdn.example.com/2.jpg']}
        title="album"
        testId="carousel-t3"
      />,
    );
    expect(screen.getAllByRole('img')).toHaveLength(2);
    expect(screen.getByTestId('carousel-t3-count')).toHaveTextContent('1 / 2');
  });

  it('reports the active slide index', () => {
    const onIndexChange = vi.fn();
    render(
      <ImageCarousel
        images={['https://cdn.example.com/1.jpg', 'https://cdn.example.com/2.jpg']}
        title="album"
        onIndexChange={onIndexChange}
        testId="carousel-t3"
      />,
    );
    act(() => {
      slideChangeHandler?.({ realIndex: 1 });
    });
    expect(onIndexChange).toHaveBeenCalledWith(1);
    expect(screen.getByTestId('carousel-t3-count')).toHaveTextContent('2 / 2');
  });
});
