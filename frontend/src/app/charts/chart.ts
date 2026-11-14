import {
  ChangeDetectionStrategy, Component, ElementRef, OnDestroy, computed, effect, input, signal, viewChild,
} from '@angular/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { ECharts, init, use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { ViewType } from '../core/types';
import { ChartInput, ChartPlan, Palette, chartPlan, fillChartData } from './options';

use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

function readPalette(element: HTMLElement): Palette {
  const style = getComputedStyle(element);
  const read = (name: string) => style.getPropertyValue(name).trim();
  return {
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => read('--series-' + n)),
    surface: read('--surface'), text: read('--text'), muted: read('--muted'), grid: read('--border'), gap: read('--gap-fill'),
  };
}

/** Grafik zu einer Ergebnistabelle. Die Tabelle daneben bleibt die vollständige Fassung. */
@Component({
  selector: 'app-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div #plot class="plot" role="img" [attr.aria-label]="label()" [style.height.px]="plan().height"
         [hidden]="!plan().option"></div>
    @for (note of plan().notes; track note) { <p class="muted small">{{ note }}</p> }
    @if (!plan().option) { <p class="muted">Für diese Darstellung gibt es nichts zu zeichnen.</p> }`,
  styles: `
    :host { display: block;
      --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
      --series-5: #e87ba4; --series-6: #008300; --series-7: #6250d6; --series-8: #e34948; --gap-fill: #e7aeaa; }
    :host-context([data-theme="dark"]) {
      --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
      --series-5: #d55181; --series-6: #008300; --series-7: #9085e9; --series-8: #e66767; --gap-fill: #8a4f52; }
    @media (prefers-color-scheme: dark) {
      :host-context(html:not([data-theme="light"])) {
        --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
        --series-5: #d55181; --series-6: #008300; --series-7: #9085e9; --series-8: #e66767; --gap-fill: #8a4f52; }
    }
    .plot { width: 100%; }
    .small { font-size: 12px; margin: 4px 0 0; }
  `,
})
export class Chart implements OnDestroy {
  readonly data = input<ChartInput>({ columns: [], rows: [] });
  readonly view = input<ViewType>('bar');
  /** Eigene Grafik statt einer aus der Tabelle (z. B. der Verlauf über die Zeit) */
  readonly build = input<((palette: Palette) => ChartPlan) | null>(null);
  readonly label = input('Grafik');
  private readonly plot = viewChild.required<ElementRef<HTMLElement>>('plot');
  private readonly theme = signal(0);
  private chart: ECharts | null = null;
  private readonly resize = new ResizeObserver(() => this.chart?.resize());
  private readonly themeWatch = new MutationObserver(() => this.theme.update((n) => n + 1));
  private readonly media = matchMedia('(prefers-color-scheme: dark)');
  private readonly onMedia = () => this.theme.update((n) => n + 1);

  protected readonly plan = computed(() => {
    this.theme();
    const palette = readPalette(this.plot().nativeElement);
    return this.build()?.(palette) ?? chartPlan(fillChartData(this.data(), this.view()), this.view(), palette);
  });

  constructor() {
    this.themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    this.media.addEventListener('change', this.onMedia);
    effect(() => {
      const plan = this.plan();
      const element = this.plot().nativeElement;
      // die Zeichenfläche erbt keine Schrift: die der Seite ausdrücklich mitgeben
      const fontFamily = getComputedStyle(element).fontFamily;
      if (!plan.option) {
        this.chart?.clear();
        return;
      }
      if (!this.chart) {
        this.chart = init(element, undefined, { height: plan.height });
        this.resize.observe(element);
      }
      this.chart.resize({ height: plan.height });
      this.chart.setOption({ animationDuration: 200, textStyle: { fontFamily }, ...plan.option }, true);
    });
  }

  ngOnDestroy(): void {
    this.resize.disconnect();
    this.themeWatch.disconnect();
    this.media.removeEventListener('change', this.onMedia);
    this.chart?.dispose();
  }
}
