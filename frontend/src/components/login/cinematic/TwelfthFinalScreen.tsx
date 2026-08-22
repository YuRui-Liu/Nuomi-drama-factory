import type { CSSProperties } from "react";
import LightRays from "@/components/login/light-rays";
import { LoginStageHeader } from "@/components/login/login-stage";
import { businessWechatQrUrl } from "./media";
import styles from "./twelfth-final-screen.module.css";

export function TwelfthFinalScreen({
  onStart,
  progress,
}: {
  onStart: () => void;
  progress: number;
}) {
  if (progress <= 0.01) return null;

  const style = {
    "--final-opacity": progress,
    "--final-offset": `${(1 - progress) * 34}px`,
  } as CSSProperties;

  return (
    <section className={styles.layer} style={style}>
      <LightRays
        className={styles.background}
        raysOrigin="top-center"
        raysColor="#ffffff"
        raysSpeed={1}
        lightSpread={0.5}
        rayLength={3}
        pulsating={false}
        fadeDistance={1}
        saturation={1}
        followMouse={false}
        mouseInfluence={0.1}
        noiseAmount={0}
        distortion={0}
      />
      <div className={styles.header}>
        <LoginStageHeader />
      </div>
      <div className={styles.content}>
        <h2>把一句设定推进成完整剧集</h2>
        <p>输入角色冲突或世界观，让 NuomiDrama 拆成可制作、可调整的镜头节点</p>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} onClick={onStart}>
            开始创作
          </button>
          <div className={styles.business}>
            <button type="button" className={styles.secondary}>
              快速申请账号
            </button>
            <div
              className={styles.businessPopover}
              role="dialog"
              aria-label="商务联系"
            >
              <div className={styles.businessPanel}>
                <img
                  src={businessWechatQrUrl}
                  alt="商务微信二维码"
                  draggable={false}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
