import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AvatarCropDialog } from '@/components/assets/character-delivery/avatar-crop-dialog';
import type { CastingCandidate } from '@/types/character-casting';

const candidate={url:'/sheet.png',crop_box:[.05,.1,.2,.6]} as CastingCandidate;
describe('avatar crop',()=>{
  it('previews and submits normalized source coordinates independent of displayed size',async()=>{
    const save=vi.fn().mockResolvedValue(undefined), close=vi.fn();
    render(<AvatarCropDialog candidate={candidate} onSave={save} onClose={close} busy={false}/>);
    expect(screen.getByAltText('裁切头像预览')).toHaveAttribute('src','/sheet.png');
    const source=screen.getByAltText('角色设定图裁切源');
    Object.defineProperties(source,{naturalWidth:{value:1200},naturalHeight:{value:800}});
    fireEvent.load(source);
    fireEvent.click(screen.getByRole('button',{name:'保存头像裁切'}));
    await waitFor(()=>expect(save).toHaveBeenCalledWith([.05,.1,.2,.6]));
    expect(close).toHaveBeenCalledOnce();
  });
  it('keeps the dialog open when the source version changes',async()=>{
    const close=vi.fn();
    render(<AvatarCropDialog candidate={candidate} onSave={vi.fn().mockRejectedValue(new Error('asset version conflict'))} onClose={close} busy={false}/>);
    fireEvent.click(screen.getByRole('button',{name:'保存头像裁切'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('asset version conflict');
    expect(close).not.toHaveBeenCalled();
  });
  it('does not invent an initial crop when automatic location failed',()=>{
    render(<AvatarCropDialog candidate={{...candidate,crop_box:null}} onSave={vi.fn()} onClose={vi.fn()} busy={false}/>);
    expect(screen.getByRole('button',{name:'保存头像裁切'})).toBeDisabled();
    expect(screen.queryByAltText('裁切头像预览')).not.toBeInTheDocument();
  });
});
