import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { TitlePositionPreview, moveTitle } from './title-position';

it('normalizes drag distance against the actual image and clamps to its edges', () => {
  expect(moveTitle({x:.5,y:.4}, {x:120,y:60}, {width:600,height:300})).toEqual({x:.7,y:.6});
  expect(moveTitle({x:.5,y:.4}, {x:-900,y:900}, {width:600,height:300})).toEqual({x:0,y:1});
  expect(moveTitle({x:.5,y:.4}, {x:50,y:50}, {width:0,height:0})).toEqual({x:.5,y:.4});
});

it('allows precise and accelerated keyboard positioning without rendering new media', () => {
  const change = vi.fn();
  render(<TitlePositionPreview src="preview.png" position={{x:.5,y:.4}} onChange={change} />);
  const handle = screen.getByRole('button', {name:'移动片名位置'});
  fireEvent.keyDown(handle, {key:'ArrowRight'});
  expect(change).toHaveBeenLastCalledWith({x:.51,y:.4});
  fireEvent.keyDown(handle, {key:'ArrowUp', shiftKey:true});
  expect(change).toHaveBeenLastCalledWith({x:.5,y:.35});
});

it('maps pointer movement to the rendered image rectangle and releases capture', () => {
  const change = vi.fn();
  render(<TitlePositionPreview src="preview.png" position={{x:.5,y:.4}} onChange={change} />);
  const image = screen.getByAltText('当前片头效果预览');
  vi.spyOn(image, 'getBoundingClientRect').mockReturnValue({x:100,y:80,left:100,top:80,right:700,bottom:380,width:600,height:300,toJSON:() => ({})});
  const handle = screen.getByRole('button', {name:'移动片名位置'});
  handle.setPointerCapture = vi.fn(); handle.hasPointerCapture = () => true; handle.releasePointerCapture = vi.fn();
  function pointer(type:string, x:number, y:number) {
    const event = new Event(type, {bubbles:true});
    Object.assign(event, {pointerId:1,button:0,clientX:x,clientY:y});
    fireEvent(handle,event);
  }
  pointer('pointerdown',400,200);
  pointer('pointermove',520,260);
  expect(change).toHaveBeenLastCalledWith({x:.7,y:.6});
  pointer('pointerup',1000,600);
  expect(change).toHaveBeenLastCalledWith({x:1,y:1});
  expect(handle.releasePointerCapture).toHaveBeenCalledWith(1);
});
