import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import {VideoProcessingConfig} from './VideoProcessingConfig';
const mocks = vi.hoisted(()=>({api:vi.fn()}));
vi.mock('@/api/client',()=>({apiCall:mocks.api}));
describe('processing model configuration',()=>{
  it('configures one capability with a form without requiring the others',async()=>{
    mocks.api.mockResolvedValue({models:{}});
    const saved = vi.fn();
    render(<VideoProcessingConfig project="p" onSaved={saved}/>);
    fireEvent.click(screen.getByText('配置处理模型'));
    await waitFor(()=>expect(screen.getByLabelText('视频延长模型 ID')).toBeEnabled());
    fireEvent.click(screen.getByLabelText('启用视频延长'));
    fireEvent.click(screen.getByText('保存配置'));
    expect(screen.getByText('请为已启用的能力填写模型 ID')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('视频延长模型 ID'),{target:{value:'extend-v1'}});
    fireEvent.change(screen.getByLabelText('延长返回内容'),{target:{value:'continuation'}});
    fireEvent.click(screen.getByText('保存配置'));
    await waitFor(()=>expect(saved).toHaveBeenCalledOnce());
    expect(mocks.api).toHaveBeenLastCalledWith('projects/p/freezone/video/processing-config',{
      method:'PUT',json:{models:{extend:{model:'extend-v1',enabled:true,output_kind:'continuation'}}},
    });
  });
});
